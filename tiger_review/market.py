"""标的行情：富途 OpenD 为主（本机已在跑），老虎行情为备，全部本地缓存。

富途历史 K 线额度按「30 天内不同代码数」计，同一代码反复取不加额度，
所以这里只拉标的（正股 / ETF / 指数）的日线和分钟线，期权价格路径用模型推算。
"""
from __future__ import annotations

import json
from bisect import bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DATA_DIR, ET

CACHE = DATA_DIR / "cache" / "futu"
INDEX_MAP = {"SPX": "US..SPX", "SPXW": "US..SPX", "NDX": "US..NDX", "NDXP": "US..NDX",
             "RUT": "US..RUT", "RUTW": "US..RUT", "VIX": "US..VIX", "XSP": "US..SPX"}
SCALE = {"XSP": 0.1}


def futu_code(symbol: str) -> str:
    return INDEX_MAP.get(symbol, f"US.{symbol}")


class Prices:
    """统一的价格查询接口。provider 失败时返回 None，不中断复盘。"""

    def __init__(self, use_futu: bool = True) -> None:
        self.use_futu = use_futu
        self._q = None
        self._daily: dict[str, list[dict]] = {}
        self._minute: dict[tuple, list[dict]] = {}
        self.errors: list[str] = []

    # ---------- 富途连接 ----------
    def _ctx(self):
        if self._q is None:
            from futu import OpenQuoteContext
            self._q = OpenQuoteContext(host="127.0.0.1", port=11111)
        return self._q

    def close(self) -> None:
        if self._q is not None:
            self._q.close()
            self._q = None

    def _kline(self, code: str, start: str, end: str, ktype, extended: bool = False) -> list[dict]:
        import time
        from futu import AuType, RET_OK
        rows: list[dict] = []
        page = None
        while True:
            time.sleep(0.55)        # 富途历史 K 线限频：30 秒 60 次
            ret, df, page = self._ctx().request_history_kline(
                code, start=start, end=end, ktype=ktype, autype=AuType.NONE, max_count=1000,
                page_req_key=page, extended_time=extended)
            if ret != RET_OK:
                raise RuntimeError(str(df))
            rows.extend(df[["time_key", "open", "high", "low", "close", "volume"]]
                        .to_dict(orient="records"))
            if page is None:
                break
        return rows

    # ---------- 日线 ----------
    def ensure_daily(self, symbol: str, start: date, end: date) -> None:
        """一次性把 [start, end] 的日线拉进缓存（已覆盖则不重复请求）。"""
        today = datetime.now(tz=ET).date()
        need_end = min(end, today)
        have = self._daily.get(symbol)
        if have:
            first, last = have[0]["time_key"][:10], have[-1]["time_key"][:10]
            if first <= start.isoformat() and (last >= need_end.isoformat() or not self.use_futu):
                return                  # 内存里已覆盖所需区间，不重读（也不覆盖调用方注入的数据）
            if not self.use_futu:
                return
        path = CACHE / "daily" / f"{symbol}.json"
        cached = json.loads(path.read_text()) if path.exists() else None
        if cached and cached["start"] <= start.isoformat() and cached["end"] >= need_end.isoformat():
            self._daily[symbol] = cached["rows"]
            return
        if not self.use_futu:
            self._daily[symbol] = cached["rows"] if cached else []
            return
        from futu import KLType
        s0 = min(start, date.fromisoformat(cached["start"])) if cached else start
        try:
            rows = self._kline(futu_code(symbol), s0.isoformat(), need_end.isoformat(), KLType.K_DAY)
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{symbol} 日线：{exc}")
            self._daily[symbol] = cached["rows"] if cached else []
            return
        k = SCALE.get(symbol, 1.0)
        for r in rows:
            for f in ("open", "high", "low", "close"):
                r[f] *= k
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"start": s0.isoformat(), "end": need_end.isoformat(),
                                    "rows": rows}))
        self._daily[symbol] = rows

    def daily(self, symbol: str) -> list[dict]:
        return self._daily.get(symbol, [])

    def close_on(self, symbol: str, d: date, exact: bool = False) -> float | None:
        """d 日收盘价；d 休市（如到期日撞上节假日）时取之前最近一个交易日，最多回看 4 天。"""
        best = None
        for r in self.daily(symbol):
            k = r["time_key"][:10]
            if k == d.isoformat():
                return float(r["close"])
            if not exact and (d - timedelta(days=4)).isoformat() <= k < d.isoformat():
                best = float(r["close"])
        return best

    def closes_between(self, symbol: str, d0: date, d1: date) -> list[tuple[date, float, float, float]]:
        """[(日期, 最高, 最低, 收盘)]，含两端。"""
        out = []
        for r in self.daily(symbol):
            d = date.fromisoformat(r["time_key"][:10])
            if d0 <= d <= d1:
                out.append((d, float(r["high"]), float(r["low"]), float(r["close"])))
        return out

    def trading_days_before(self, symbol: str, d: date, n: int) -> list[tuple[date, float]]:
        rows = [(date.fromisoformat(r["time_key"][:10]), float(r["close"]))
                for r in self.daily(symbol)]
        rows = [x for x in rows if x[0] < d]
        return rows[-n:]

    def trading_days_after(self, symbol: str, d: date, n: int) -> list[tuple[date, float]]:
        rows = [(date.fromisoformat(r["time_key"][:10]), float(r["close"]))
                for r in self.daily(symbol)]
        return [x for x in rows if x[0] > d][:n]

    # ---------- 分钟线 ----------
    def minute(self, symbol: str, d: date) -> list[dict]:
        key = (symbol, d)
        if key in self._minute:
            return self._minute[key]
        path = CACHE / "1m" / symbol / f"{d.isoformat()}.json"
        if path.exists():
            rows = json.loads(path.read_text())
        elif self.use_futu and d <= datetime.now(tz=ET).date():
            from futu import KLType
            try:
                rows = self._kline(futu_code(symbol), d.isoformat(), d.isoformat(), KLType.K_1M,
                                   extended=True)
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"{symbol} {d} 分钟线：{exc}")
                rows = []
            k = SCALE.get(symbol, 1.0)
            for r in rows:
                for f in ("open", "high", "low", "close"):
                    r[f] *= k
            if rows and d < datetime.now(tz=ET).date():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows))
        else:
            rows = []
        self._minute[key] = rows
        return rows

    def price_at(self, symbol: str, ts: datetime) -> float | None:
        """ts 时刻的标的价格：取不晚于 ts 的最后一根分钟线收盘，缺分钟线退到日线收盘。"""
        rows = self.minute(symbol, ts.date())
        if rows:
            keys = [r["time_key"] for r in rows]
            stamp = ts.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")
            i = bisect_right(keys, stamp)
            return float(rows[i - 1]["close"]) if i > 0 else float(rows[0]["open"])
        return self.close_on(symbol, ts.date())

    def session_so_far(self, symbol: str, ts: datetime) -> dict | None:
        """常规时段开盘到 ts 的开、高、低（用于看进场位置在当日区间的哪里）。"""
        rows = self.minute(symbol, ts.date())
        if not rows:
            return None
        stamp = ts.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")
        day = ts.date().isoformat()
        rth = [r for r in rows if f"{day} 09:31:00" <= r["time_key"] <= f"{day} 16:00:00"]
        upto = [r for r in rth if r["time_key"] <= stamp]
        if not upto:
            return None
        return {"open": float(upto[0]["open"]), "high": max(float(r["high"]) for r in upto),
                "low": min(float(r["low"]) for r in upto)}
