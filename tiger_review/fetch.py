"""从老虎 OpenAPI 拉取订单、持仓、净值历史，落成本地 JSON。

只读：本模块不调用任何下单 / 改单 / 撤单接口。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR, ET, PROPS_DIR

RAW_DIR = DATA_DIR / "raw"
WINDOW_DAYS = 89          # 官方：查询区间不能超过 90 天
PAGE_LIMIT = 300          # get_orders 每页上限
MIN_INTERVAL_SEC = 0.6    # 订单接口限流 120 次/分钟，留余量


def _plain(value: Any) -> Any:
    """把 SDK 对象（含 __slots__ 的 Order、Contract、枚举）转成可 JSON 序列化的值。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "value") and value.__class__.__module__.startswith("tigeropen"):
        return value.value
    out: dict[str, Any] = {}
    names: Iterable[str] = []
    if hasattr(value, "__slots__"):
        names = [n for n in value.__slots__]
    if hasattr(value, "__dict__"):
        names = list(names) + list(vars(value).keys())
    for name in names:
        key = name.lstrip("_")
        try:
            out[key] = _plain(getattr(value, name))
        except AttributeError:
            continue
    return out if out else str(value)


def _contract_dict(contract: Any) -> dict:
    if contract is None:
        return {}
    fields = ["symbol", "sec_type", "currency", "expiry", "strike", "put_call", "multiplier",
              "identifier", "market", "local_symbol"]
    return {f: _plain(getattr(contract, f, None)) for f in fields}


def order_to_dict(order: Any) -> dict:
    """SDK Order → 精简 dict（只保留复盘需要的字段，不含 secret_key）。"""
    keep = ["account", "id", "order_id", "parent_id", "action", "order_type", "quantity",
            "filled", "avg_fill_price", "commission", "gst", "realized_pnl", "limit_price",
            "aux_price", "time_in_force", "outside_rth", "order_time", "trade_time",
            "update_time", "status", "source", "is_open", "combo_type", "liquidation",
            "trading_session_type", "user_mark", "remaining", "filled_cash_amount", "charges"]
    d = {k: _plain(getattr(order, k, None)) for k in keep}
    d["contract"] = _contract_dict(getattr(order, "contract", None))
    legs = getattr(order, "contract_legs", None) or []
    d["contract_legs"] = [_plain(leg) for leg in legs]
    return d


def _to_ms(day: date, end: bool = False) -> int:
    """日期 → 美东当日 00:00 的毫秒时间戳（end=True 时取次日 00:00，即开区间右端）。"""
    d = day + timedelta(days=1) if end else day
    return int(datetime(d.year, d.month, d.day, tzinfo=ET).timestamp() * 1000)


class TigerFetcher:
    """封装只读查询。凭据目录默认 ~/.config/tiger/。"""

    def __init__(self, props_dir: Path = PROPS_DIR, account: str | None = None) -> None:
        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.trade.trade_client import TradeClient

        if not (props_dir / "tiger_openapi_config.properties").exists():
            raise FileNotFoundError(
                f"没找到 {props_dir}/tiger_openapi_config.properties。"
                "请从老虎开发者页面导出配置文件放到这个目录。")
        cfg = TigerOpenClientConfig(props_path=str(props_dir) + "/")
        cfg.timezone = "US/Eastern"
        if account:
            cfg.account = account
        self.config = cfg
        self.account = cfg.account
        self.trade = TradeClient(cfg)
        self._quote = None
        self._last_call = 0.0
        self.token_refreshed = self._maybe_refresh_token()

    def _maybe_refresh_token(self) -> bool:
        """TBHK 牌照的 token 30 天过期；签发超过 7 天就换新，写回凭据目录里的 token 文件。"""
        cfg = self.config
        if not getattr(cfg, "token", None):
            return False
        cfg.token_refresh_duration = 7 * 86400
        try:
            if cfg.should_token_refresh():
                self.trade.refresh_token()
                return True
        except Exception:  # noqa: BLE001  续期失败不影响本次取数，旧 token 仍在有效期内
            return False
        return False

    @property
    def quote(self):
        """行情客户端，按需创建（小账户可能没开行情权限，失败不影响复盘主流程）。"""
        if self._quote is None:
            from tigeropen.quote.quote_client import QuoteClient
            self._quote = QuoteClient(self.config)
        return self._quote

    def _throttle(self) -> None:
        wait = MIN_INTERVAL_SEC - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    # ---------- 账户 ----------
    def accounts(self) -> list[dict]:
        self._throttle()
        return [_plain(a) for a in (self.trade.get_managed_accounts() or [])]

    def positions(self) -> list[dict]:
        """期权 + 正股持仓快照。"""
        out = []
        for sec in ("OPT", "STK"):
            self._throttle()
            for p in self.trade.get_positions(sec_type=sec) or []:
                d = _plain(p)
                d["contract"] = _contract_dict(getattr(p, "contract", None))
                out.append(d)
        return out

    def assets(self) -> dict:
        self._throttle()
        try:
            return _plain(self.trade.get_prime_assets())
        except Exception:  # 环球账户用 get_assets
            self._throttle()
            return {"assets": _plain(self.trade.get_assets())}

    def nav_history(self, start: date, end: date) -> dict:
        """账户每日资产与盈亏历史（get_analytics_asset）。"""
        self._throttle()
        res = self.trade.get_analytics_asset(start_date=start.isoformat(),
                                             end_date=end.isoformat())
        return _plain(res) or {}

    # ---------- 订单 ----------
    def orders(self, start: date, end: date) -> list[dict]:
        """拉 [start, end] 区间所有有成交的订单（含部分成交后撤单、组合单）。

        按 89 天切窗、每窗内用 page_token 翻页，再按订单 id 去重。
        """
        seen: dict[str, dict] = {}
        cur = start
        while cur <= end:
            win_end = min(cur + timedelta(days=WINDOW_DAYS - 1), end)
            # 不传证券类型时服务端是否返回全部类型没有文档保证，期权 / 组合 / 正股各拉一遍再去重
            for sec in (None, "OPT", "MLEG", "STK"):
                token: str | None = ""
                while token is not None:
                    self._throttle()
                    resp = self.trade.get_orders(sec_type=sec, start_time=_to_ms(cur),
                                                 end_time=_to_ms(win_end, True),
                                                 limit=PAGE_LIMIT, page_token=token)
                    items = getattr(resp, "result", resp) or []
                    for o in items:
                        d = order_to_dict(o)
                        if (d.get("filled") or 0) > 0:
                            seen[str(d["id"])] = d
                    token = getattr(resp, "next_page_token", None) or None
            cur = win_end + timedelta(days=1)
        return sorted(seen.values(), key=lambda d: d.get("trade_time") or d.get("order_time") or 0)

    def filled_orders_check(self, start: date, end: date) -> int:
        """用 get_filled_orders 独立数一遍成交单，供 doctor 对账。"""
        n = 0
        cur = start
        while cur <= end:
            win_end = min(cur + timedelta(days=WINDOW_DAYS - 1), end)
            self._throttle()
            n += len(self.trade.get_filled_orders(start_time=_to_ms(cur),
                                                  end_time=_to_ms(win_end, True)) or [])
            cur = win_end + timedelta(days=1)
        return n

    # ---------- 行情（可选增强） ----------
    def option_daily_bars(self, identifiers: list[str], start: date, end: date) -> list[dict]:
        if not identifiers:
            return []
        self._throttle()
        df = self.quote.get_option_bars(identifiers, begin_time=_to_ms(start),
                                        end_time=_to_ms(end, True), period="day")
        return [] if df is None else json.loads(df.to_json(orient="records"))

    def stock_bars(self, symbol: str, start: date, end: date, period: str = "day") -> list[dict]:
        self._throttle()
        df = self.quote.get_bars([symbol], period=period, begin_time=_to_ms(start),
                                 end_time=_to_ms(end, True), limit=1200)
        return [] if df is None else json.loads(df.to_json(orient="records"))

    def earnings_calendar(self, start: date, end: date) -> list[dict]:
        self._throttle()
        res = self.quote.get_corporate_earnings_calendar("US", start.isoformat(), end.isoformat())
        if res is None:
            return []
        if hasattr(res, "to_json"):
            return json.loads(res.to_json(orient="records"))
        return _plain(res)


def save_json(name: str, payload: Any) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    path.chmod(0o600)
    return path


def load_json(name: str, default: Any = None) -> Any:
    path = RAW_DIR / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def merge_orders(existing: list[dict], new: list[dict]) -> list[dict]:
    """增量合并：同 id 以新拉取的为准。"""
    by_id = {str(o["id"]): o for o in existing}
    for o in new:
        by_id[str(o["id"])] = o
    return sorted(by_id.values(), key=lambda d: d.get("trade_time") or d.get("order_time") or 0)
