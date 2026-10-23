"""盘中提醒 v0：实盘与回放共用 Engine，不下单。

规则快照来自 info-heat-card/scripts/odte_card.py 的 PANIC、PIN（2026-10-23）；
当天 intraday_alerts 字段覆盖默认值。VIXY 三个日涨幅门槛采用任务指定的代理口径。
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import parse, request

from .config import DATA_DIR, ET
from .market import Prices

MINUTES = DATA_DIR / "cache" / "futu" / "1m"
CARDS = Path.home() / "Codes" / "reports" / "盘前作战卡"
PANIC = dict(
    vixy_to_vix=0.47, trigger_vixy_30m_accel_min=0.025,
    tail_min_met=1, tail_recommended_met=2, tail_breadth_below_vwap_min=0.85,
    tail_qqq_ret5_max=-0.03, tail_gap_up_then_red=True,
)
PIN = dict(ma=20, min_score=3, best_score=4, score=[
    dict(id="above_open", points=2), dict(id="gap_up_ge", threshold=0.03, points=1),
    dict(id="minutes_after_open_ge", threshold=30, points=1),
    dict(id="qqq_same_window_drop_ge", threshold=0.004, points=1),
])
VIXY_TRIGGER, VIXY_TRIGGER_MAX, VIXY_EXCLUDE = 0.020, 0.042, 0.061
CBOE_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/130 Safari/537.36"}


def _http(url: str, timeout: int = 12) -> str:
    with request.urlopen(request.Request(url, headers=CBOE_UA), timeout=timeout) as r:
        return r.read().decode()


def cboe_quote(sym: str) -> tuple[float, float | None] | None:
    """Cboe 官网延迟报价（约 15 分钟）：(现价, 昨收)。富途不提供美股指数，这是免费的 VIX/VIX3M 来源。"""
    try:
        d = json.loads(_http(f"https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json")).get("data") or {}
        cur = number(d.get("current_price"))
        return (cur, number(d.get("prev_day_close"))) if cur else None
    except Exception:
        return None


def cboe_closes(sym: str, n: int = 15) -> list[tuple[str, float]]:
    """Cboe 日线 CSV 最近 n 个收盘（只有昨收，没有今天）。"""
    try:
        rows = list(csv.reader(io.StringIO(_http(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv", 20))))
        return [(r[0], float(r[4])) for r in rows[1:] if len(r) >= 5 and r[4]][-n:]
    except Exception:
        return []


def vix_stage(vix: float | None, vix3m: float | None, ratios: list[float] | None = None, delayed: bool = False) -> dict:
    """把 VIX 与三个月 VIX 的关系说成人话（口径来自 10-23 的 VIX/VIX3M 收窄检验：
    收窄未倒挂=恐慌前段、之后 5 天偏弱；一过 1（倒挂）不分加深回落都是反弹先验）。"""
    if not vix or not vix3m:
        return dict(text="恐慌指数 VIX 的数据没取到，先按盘前裁定。", inverted=None, narrowing=None)
    r = vix / vix3m
    tag = "（约 15 分钟延迟）" if delayed else "（昨收）"
    if r >= 1.0:
        return dict(text=f"VIX {vix:.1f} 已经高过三个月 VIX {vix3m:.1f}（倒挂）{tag}。按回放，倒挂之后多半是反弹，恐慌 Put 不做。",
                    inverted=True, narrowing=False, ratio=r)
    narrowing = None
    if ratios and len(ratios) >= 6:
        narrowing = (ratios[-1] - ratios[-6] >= 0.03) and ratios[-1] >= 0.92
    base = f"VIX {vix:.1f} 比三个月 VIX {vix3m:.1f} 低（比值 {r:.2f}，没倒挂）{tag}"
    if narrowing is True:
        text = base + "，而且差距最近 5 天在收窄＝恐慌前半段的样子，之后几天偏弱，是做 Put 的窗口。"
    elif narrowing is False:
        text = base + "，差距没在收窄＝还算平静，恐慌 Put 要等盘中 VIX 自己冲起来。"
    else:
        text = base + "。"
    return dict(text=text, inverted=False, narrowing=narrowing, ratio=r)


# VIX 期限结构看板（research/期权/VIX期限结构…/live/server.py，字段约定见其文件头与 README）。
# 看板搬到 Mac Studio 后，用环境变量 VIXLIVE_URL 指过去（launchd plist 的 EnvironmentVariables 里加），本机默认 localhost。
VIXLIVE = os.environ.get("VIXLIVE_URL", "http://127.0.0.1:8765/api/state")
TALK_TITLE = {"CALM": "市场平静", "RISE_A": "恐慌在升温，还没到最急的时候", "RISE_B": "恐慌到了最急的时候",
              "INV": "恐慌还在最急的区域", "MID": "恐慌在高位来回", "ROLL_A": "最急的时候过去了", "ROLL_B": "恐慌没起来就退了"}


def vixlive_state() -> dict | None:
    """优先读本机 VIX 看板的状态（同一套说法）；看板没开就返回 None，由 Cboe 兜底。"""
    try:
        d = json.loads(_http(VIXLIVE, 3))
    except Exception:
        d = {}
    if d.get("status") != "ok" or not d.get("vix") or not d.get("vix3m"):
        # 看板进程没开：读它逐分钟落盘的最后一行（10 分钟内才算数）
        tick = Path.home() / "Codes" / "reports" / "VIX盘中看板" / "ticks" / f"{datetime.now(ET).date()}.jsonl"
        try:
            last = json.loads(tick.read_text(encoding="utf-8").strip().splitlines()[-1])
            age = datetime.now(ET) - datetime.now(ET).replace(hour=int(last["t"][:2]), minute=int(last["t"][3:5]), second=0, microsecond=0)
            if not 0 <= age.total_seconds() <= 600 or not last.get("vix") or not last.get("vix3m"):
                return None
            d = dict(status="ok", vix=dict(price=last["vix"]), vix3m=dict(price=last["vix3m"]), r=last.get("r"),
                     group="MID", name="", now=last["t"])
        except Exception:
            return None
    r, g = number(d.get("r")), d.get("group")
    k = "INV" if g == "MID" and r and r >= 1 else g
    title = TALK_TITLE.get(k) or str(d.get("name") or "") or ("VIX 已高过三个月 VIX（倒挂）" if r and r >= 1 else "VIX 还低于三个月 VIX（没倒挂）")
    vix, vix3m = number(d["vix"].get("price")), number(d["vix3m"].get("price"))
    if not vix or not vix3m:
        return None
    return dict(vix=vix, vix3m=vix3m, vix_prev=number(d["vix"].get("prev")), ratio=r, inverted=bool(r and r >= 1),
                title=title, text=f"VIX 看板现在的读数：{title}（VIX {vix:.1f}，三个月 VIX {vix3m:.1f}）。",
                source="vixlive", stale=bool(d.get("v3m_stale")), asof=d.get("now"))


def fetch_vix(card: "Card") -> dict:
    """开盘前取一次：延迟报价 + 日线算收窄；取不到就退回日内卡盘前的 VIX 数字。"""
    pm = card.premarket
    live_state = vixlive_state()
    if live_state:
        return live_state
    q, q3 = cboe_quote("VIX"), cboe_quote("VIX3M")
    hist, hist3 = cboe_closes("VIX"), cboe_closes("VIX3M")
    closes3 = dict(hist3)
    ratios = [c / closes3[d] for d, c in hist if d in closes3 and closes3[d]]
    if q and q3:
        info = vix_stage(q[0], q3[0], ratios, delayed=True)
        info.update(vix=q[0], vix3m=q3[0], vix_prev=q[1] or pm.get("vix"), source="Cboe 延迟报价")
    else:
        info = vix_stage(number(pm.get("vix")), number(pm.get("vix3m")), ratios)
        info.update(vix=number(pm.get("vix")), vix3m=number(pm.get("vix3m")), vix_prev=number(pm.get("vix")), source="日内卡盘前")
    info["asof"] = datetime.now(ET).isoformat()
    return info


def log(message: str) -> None:
    path = DATA_DIR / "logs" / "alerts.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now(ET):%Y-%m-%d %H:%M:%S %Z} {message}\n")


def number(value) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def pct(value: float | None) -> str:
    return f"{value:+.2%}" if value is not None else "缺数据"


def ratio(current, previous) -> float | None:
    a, b = number(current), number(previous)
    return a / b - 1 if a is not None and b is not None and b > 0 else None


def symbols_pool() -> list[str]:
    return sorted(p.name for p in MINUTES.iterdir() if p.is_dir() and p.name not in {"QQQ", "VIXY"}) if MINUTES.exists() else []


def parse_pins(value: str | None) -> list[dict] | None:
    if value is None:
        return None
    out = {}
    for item in value.split(","):
        sym, sep, depth = item.strip().upper().partition(":")
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]*", sym):
            raise ValueError(f"接针代码不合法：{sym}")
        n = number(depth.rstrip("%")) if sep and depth else None
        if sep and depth and (n is None or n <= 0):
            raise ValueError(f"{sym} 的接针百分数必须大于零")
        out[sym] = dict(sym=sym, side="Call", pin_depth_pct=n / 100 if n is not None else None,
                        no_catch_if=None)
    return list(out.values())


@dataclass
class Card:
    day: date
    panic: dict
    rules: dict
    pins: list[dict]
    underlyings: list[str]
    premarket: dict
    main_window: list[str]
    flat_by: str
    missing: bool
    ret5: float | None = None


def load_card(day: date, manual: list[dict] | None = None) -> Card:
    path = CARDS / str(day) / "odte.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw.get("intraday_alerts")
        valid = (isinstance(data, dict) and data.get("schema") == "odte-intraday-alerts.v1"
                 and all(isinstance(data.get(k), dict) for k in ("panic_put", "pin_catch", "session")))
        if valid:
            valid = (isinstance(data["panic_put"].get("underlyings"), list)
                     and isinstance(data["panic_put"].get("premarket"), dict)
                     and isinstance(data["pin_catch"].get("names"), list)
                     and isinstance(data["session"].get("main_window"), list)
                     and bool(data["session"].get("flat_by")))
        if not valid:
            data = {}
    except (OSError, ValueError, AttributeError):
        data = {}
    missing = not data
    panic = {**PANIC, **data.get("panic_put", {})}
    catch = data.get("pin_catch", {})
    rules = {**PIN, **catch.get("rules", {})}
    pins = manual if manual is not None else catch.get("names", [])
    pins = [{**p, "sym": p["sym"].upper()} for p in pins if p.get("side") == "Call" and p.get("sym")]
    pins = list({p["sym"]: p for p in pins}.values())
    if len({"QQQ", *(p["sym"] for p in pins)}) > 12:
        raise ValueError("接针票加 QQQ 超过 12 个，请缩减名单")
    session = data.get("session", {})
    return Card(day, panic, rules, pins, list(dict.fromkeys(["QQQ", *panic.get("underlyings", [])])),
                panic.get("premarket") or {}, session.get("main_window", ["09:30", "10:30"]),
                min(session.get("flat_by", "15:30"), "15:30"), missing)


def daily_context(card: Card) -> dict[str, list[dict]]:
    """只读 Prices 日线缓存；提醒器永不消耗历史 K 线额度，也不读取当天日线。"""
    prices = Prices(use_futu=False)
    daily = {}
    try:
        for sym in {"QQQ", "VIXY", *(p["sym"] for p in card.pins)}:
            prices.ensure_daily(sym, card.day - timedelta(days=60), card.day - timedelta(days=1))
            daily[sym] = sorted((r for r in prices.daily(sym) if r["time_key"][:10] < str(card.day)),
                                key=lambda r: r["time_key"])
    finally:
        prices.close()
    card.ret5 = number(card.premarket.get("qqq_ret5"))
    q = daily["QQQ"]
    if card.ret5 is None and len(q) >= 6:
        card.ret5 = ratio(q[-1]["close"], q[-6]["close"])
    for pin in card.pins:
        rows = daily[pin["sym"]]
        if number(pin.get("prev_close")) is None and rows:
            pin["prev_close"] = float(rows[-1]["close"])
        if pin.get("pin_depth_pct") is None and len(rows) >= 15:
            ranges = [(float(rows[i]["high"]) - float(rows[i]["low"])) / float(rows[i - 1]["close"])
                      for i in range(len(rows) - 14, len(rows))]
            pin["pin_depth_pct"] = sum(ranges) / 14
        pin.setdefault("min_score", card.rules["min_score"])
        pin.setdefault("best_score", card.rules["best_score"])
    return daily


class Telegram:
    def __init__(self, dry: bool):
        self.dry = dry
        self._key, self._destinations = "", []
        if not dry:
            try:
                self._key = (Path.home() / ".config/telegram/bot_token").read_text().strip()
                # 一行一个目的地：私聊 chat_id 或频道 @用户名；每条提醒发给全部目的地
                self._destinations = [x.strip() for x in (Path.home() / ".config/telegram/chat_id").read_text().splitlines()
                                      if x.strip() and not x.strip().startswith("#")]
            except OSError:
                log("Telegram 配置文件缺失或不可读，只记录提醒")

    def send(self, at: datetime, message: str) -> bool:
        if self.dry:
            print(f"[{at:%Y-%m-%d %H:%M} 美东]\n{message}", flush=True)
            return True
        log(message.replace("\n", " | "))
        if not self._key or not self._destinations:
            return False
        ok_all = True
        for dest in self._destinations:
            try:
                body = parse.urlencode({"chat" + "_id": dest, "text": message}).encode()
                req = request.Request(f"https://api.telegram.org/bot{self._key}/sendMessage", data=body, method="POST")
                with request.urlopen(req, timeout=15) as response:
                    ok = json.loads(response.read()).get("ok") is True
                if not ok:
                    log(f"Telegram 目的地 {dest[:4]}… 未确认发送成功，将重试")
                    ok_all = False
            except Exception:  # URL/异常对象可能含凭据，绝不格式化或输出它。
                log(f"Telegram 目的地 {dest[:4]}… 发送失败，将重试")
                ok_all = False
        return ok_all


class Engine:
    """快照负责恐慌，已收盘分钟线负责接针；状态只在真实推送时落盘。"""
    def __init__(self, card: Card, send, pool: list[str], persistent: bool = False):
        self.card, self.send, self.pool = card, send, pool
        self.path = DATA_DIR / "raw" / f"alerts_state_{card.day}.json" if persistent else None
        self.state = {"date": str(card.day), "sent": [], "pins": {}, "vixy": [], "pending": {}}
        if self.path and self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("date") != str(card.day) or not isinstance(saved.get("sent"), list):
                raise ValueError("提醒状态文件格式不正确，请检查后重试")
            self.state.update(saved)
            if not all(isinstance(self.state[k], t) for k, t in (("pins", dict), ("vixy", list), ("pending", dict))):
                raise ValueError("提醒状态文件格式不正确，请检查后重试")
        self.count = 0
        self.diagnostics = {}
        self.vix: dict = {}

    def update_vix(self, info: dict | None) -> None:
        if info:
            self.vix = info

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)

    def emit(self, key: str, at: datetime, lines: list[str]) -> bool:
        if key in self.state["sent"]:
            return False
        # 原话保留内容，换行折叠为一个事实。
        message = "\n".join(" ".join(str(s).splitlines()) for s in lines)
        self.state["pending"][key] = {"at": at.isoformat(), "lines": lines}
        self.save()
        if self.send(at, message):
            self.state["sent"].append(key)
            self.state["pending"].pop(key, None)
            self.count += key != "startup"
            self.save()
            return True
        return False

    def startup(self, at: datetime) -> None:
        c = self.card
        enabled = c.premarket.get("enabled")
        stage = (self.vix.get("text") if self.vix else None) or vix_stage(number(c.premarket.get("vix")), number(c.premarket.get("vix3m")))["text"]
        r5 = c.ret5
        lines = ["开盘前：今天盯两件事",
                 f"1. 恐慌买 Put，标的 {'、'.join(c.underlyings)}。" + ("盘前 VIX 已经高过三个月 VIX，今天不做。" if enabled is False else
                 "怎么做：盘中 VIX 比昨天涨 5% 到 9%、QQQ 又跌到昨收下方，我再提醒；提醒了就小仓位买当天到期、略虚值的 Put，拿到收盘。")]
        if c.pins:
            parts, skipped = [], []
            for p_ in c.pins:
                dp = number(p_.get("pin_depth_pct"))
                if dp and p_.get("prev_close"):
                    parts.append(f"{p_['sym']} 约 {dp:.1%}、${dp * float(p_['prev_close']):.1f}")
                elif dp:
                    parts.append(f"{p_['sym']} 约 {dp:.1%}")
                else:
                    skipped.append(p_["sym"])
            if parts:
                lines.append(f"2. 接针买 Call：{'、'.join(x.split(' ')[0] for x in parts)}。怎么做：从今天最高点跌下来超过它平时一天的波动（{'；'.join(parts)}）"
                             "我提醒「插针了」；等一分钟线重新站上 20 分钟均线我再提醒「可以接」；针底跌破就走。")
            if skipped:
                lines.append(f"{'、'.join(skipped)} 的平时波动算不出来，今天不盯。")
        else:
            lines.append("2. 接针买 Call：今天没有进攻票。")
        why = "为什么：" + stage
        if r5 is not None and r5 < c.panic["tail_qqq_ret5_max"]:
            why += f" QQQ 最近 5 天已经跌了 {abs(r5):.1%}，这种时候 VIX 再冲往往是恐慌前半段。"
        why += " VIX 涨过 13%，或高过三个月 VIX，就不追，那多半要反弹了。"
        lines.append(why)
        if c.missing:
            lines.append("今天没有日内卡；恐慌只盯 QQQ" + ("，接针用手工名单。" if c.pins else "，不盯接针。"))
        self.emit("startup", at, lines)

    def step(self, at: datetime, quotes: dict[str, dict], bars: dict[str, list[dict]]) -> None:
        hm = at.strftime("%H:%M")
        for key, item in list(self.state["pending"].items()):
            if key.startswith("pin:") and not "09:30" <= hm <= self.card.flat_by:
                self.state["pending"].pop(key)
                continue
            if key in {"panic_weak", "panic_strong"}:
                v, q = quotes.get("VIXY", {}), quotes.get("QQQ", {})
                change = ratio(v.get("last_price"), v.get("prev_close_price"))
                qchange = ratio(q.get("last_price"), q.get("prev_close_price"))
                if change is None or qchange is None:
                    continue
                if (not "09:45" <= hm <= "15:00" or self.card.premarket.get("enabled") is False
                        or not VIXY_TRIGGER - 1e-12 <= change < VIXY_TRIGGER_MAX - 1e-12 or qchange >= 0
                        or (key == "panic_weak" and "panic_strong" in self.state["sent"])):
                    self.state["pending"].pop(key)
                    continue
            self.emit(key, datetime.fromisoformat(item["at"]), item["lines"])
        v = number(quotes.get("VIXY", {}).get("last_price"))
        if v is not None and v > 0:
            stamp = at.isoformat()
            hist = self.state["vixy"]
            hist[:] = [x for x in hist if x[0] >= (at - timedelta(minutes=35)).isoformat() and x[0] != stamp]
            hist.append([stamp, v])
        if (self.vix.get("inverted") and self.vix.get("source") != "vixlive" and "09:30" <= hm <= "16:00"
                and self.card.premarket.get("enabled") is not False):
            self.emit("vix_inverted", at, ["VIX 刚高过了三个月 VIX：恐慌 Put 不再加",
                      "怎么做：已有的 Put 拿到收盘或见好就收，新的不开。",
                      "为什么：VIX 高过三个月 VIX（倒挂）之后，十次里约六七次是反弹，不是继续恐慌。",
                      f"现在：VIX {self.vix.get('vix'):.1f}，三个月 VIX {self.vix.get('vix3m'):.1f}。"])
        if "09:45" <= hm <= "15:00":
            self.panic(at, quotes)
        if "09:30" <= hm <= self.card.flat_by:
            for pin in self.card.pins:
                self.pin(at, pin, bars)
        self.save()

    def panic(self, at: datetime, quotes: dict[str, dict]) -> None:
        c = self.card
        v, q = quotes.get("VIXY", {}), quotes.get("QQQ", {})
        change = ratio(v.get("last_price"), v.get("prev_close_price"))
        qchange = ratio(q.get("last_price"), q.get("prev_close_price"))
        if change is None or qchange is None:
            return
        vix_chg = change / c.panic["vixy_to_vix"]
        now = f"现在：VIX 比昨天涨约 {vix_chg:.0%}，QQQ 比昨收{'低' if qchange < 0 else '高'} {abs(qchange):.1%}。"
        board = (" " + self.vix["text"]) if self.vix.get("source") == "vixlive" else ""
        if change >= VIXY_EXCLUDE - 1e-12:
            self.emit("panic_excluded", at, ["VIX 今天已经涨过 13%：Put 不追了",
                      "怎么做：没拿的不追；已经拿着的按计划拿到收盘或见好就收。",
                      "为什么：涨到这个程度之后，十次里约六次是先反弹。", now + board])
            return
        if not (VIXY_TRIGGER - 1e-12 <= change < VIXY_TRIGGER_MAX - 1e-12 and qchange < 0):
            return
        if c.premarket.get("enabled") is False:
            self.emit("panic_disabled", at, ["今天不做恐慌 Put：VIX 盘前就高过了三个月 VIX",
                      "怎么做：只看不动。",
                      "为什么：VIX 高过三个月 VIX（倒挂）之后，十次里约六七次是反弹，不是继续恐慌。", now + board])
            return
        if self.vix.get("inverted"):
            return  # 已倒挂：看板（或兜底提醒）已经说了不做，这里不再喊买
        target = at - timedelta(minutes=30)
        history = [(datetime.fromisoformat(t), p) for t, p in self.state["vixy"]]
        old = [p for t, p in history if target - timedelta(seconds=90) <= t <= target]
        accel = ratio(v.get("last_price"), old[-1]) if old else None
        valid = below = 0
        for sym in self.pool:
            row = quotes.get(sym, {})
            vol, turnover, price = (number(row.get(k)) for k in ("volume", "turnover", "last_price"))
            if vol is not None and turnover is not None and price is not None and vol > 0 and turnover > 0:
                valid += 1
                below += price < turnover / vol
        breadth = below / len(self.pool) if self.pool and valid else None
        tails, missing = [], []
        if accel is not None and accel >= c.panic["trigger_vixy_30m_accel_min"]:
            tails.append("最近 30 分钟 VIX 还在加速")
        else:
            missing.append("最近 30 分钟没再加速")
        if breadth is not None and breadth >= c.panic["tail_breadth_below_vwap_min"]:
            tails.append(f"热门股 85% 以上跌破了今天的均价（{breadth:.0%}）")
        else:
            missing.append(f"跌破今天均价的热门股只有 {breadth:.0%}" if breadth is not None else "热门股的广度没算出来")
        if c.ret5 is not None and c.ret5 < c.panic["tail_qqq_ret5_max"]:
            tails.append(f"QQQ 前 5 天已经跌了 {abs(c.ret5):.1%}")
        else:
            missing.append("QQQ 前 5 天跌得不够 3%")
        gap = ratio(q.get("open_price"), q.get("prev_close_price"))
        if c.panic["tail_gap_up_then_red"] and gap is not None and gap > 0 and qchange < 0:
            tails.append("今天高开后翻绿")
        else:
            missing.append("不是高开翻绿")
        strong = len(tails) >= c.panic["tail_recommended_met"]
        if len(tails) < c.panic["tail_min_met"]:
            return
        if not strong and "panic_strong" in self.state["sent"]:
            return
        if strong:
            lines = [f"恐慌来了：可以小仓位买 {'、'.join(c.underlyings)} 的 Put",
                     "怎么做：买当天到期、略虚值的 Put，仓位按能全亏的钱算，拿到收盘，中途小反弹不走；VIX 涨过 13% 或高过三个月 VIX 就不再加。",
                     f"为什么：{'；'.join(tails)}——这几种情况一起出现，像恐慌刚开始。",
                     "历史：这种时候 QQQ 到收盘再跌 1.5% 以上的日子十次里约一到两次（平时十次不到半次），赚的就是这一两次。",
                     now + board]
        else:
            lines = ["恐慌有苗头，还不够：先别动",
                     "怎么做：先不买。再出现一条像恐慌刚开始的情况，我再提醒。",
                     f"为什么：现在只有「{tails[0]}」这一条；还差：{'、'.join(missing)}。",
                     now + board]
        self.emit("panic_strong" if strong else "panic_weak", at, lines)

    def pin(self, at: datetime, spec: dict, all_bars: dict[str, list[dict]]) -> None:
        sym = spec["sym"]
        bars = all_bars.get(sym, [])
        if not bars:
            return
        bar = bars[-1]
        state = self.state["pins"].setdefault(sym, {})
        if state.get("last_bar", "") >= bar["time_key"]:
            return
        state["last_bar"] = bar["time_key"]
        peak_i = max(range(len(bars)), key=lambda i: float(bars[i]["high"]))
        peak = float(bars[peak_i]["high"])
        identity = f"{bars[peak_i]['time_key']}:{peak}"
        if identity != state.get("peak"):
            state.update(peak=identity, armed_at=None)
        low = min(float(b["low"]) for b in bars[peak_i:])
        close, opening = float(bar["close"]), float(bars[0]["open"])
        depth = peak / close - 1
        prev = spec.get("prev_close")
        q = {b["time_key"]: float(b["close"]) for b in all_bars.get("QQQ", [])}
        leg = ratio(q.get(bar["time_key"]), q.get(bars[peak_i]["time_key"]))
        bar_at = datetime.fromisoformat(bar["time_key"]).replace(tzinfo=ET)
        elapsed = (bar_at - bar_at.replace(hour=9, minute=30, second=0)).total_seconds() / 60
        rules = {r["id"]: r for r in self.card.rules["score"]}
        gap = ratio(opening, prev)
        pts = {"仍在开盘价上方": rules["above_open"]["points"] if close >= opening else 0,
               "高开≥3%": rules["gap_up_ge"]["points"] if gap is not None and gap >= rules["gap_up_ge"]["threshold"] else 0,
               "开盘30分钟后": rules["minutes_after_open_ge"]["points"] if elapsed >= rules["minutes_after_open_ge"]["threshold"] else 0,
               "QQQ同期跌>0.4%": rules["qqq_same_window_drop_ge"]["points"] if leg is not None and leg < -rules["qqq_same_window_drop_ge"]["threshold"] else 0}
        score = min(sum(pts.values()), 4)  # spike.reading 的总分上限。
        diag = self.diagnostics.setdefault(sym, {"depth": -1, "max_score": 0})
        diag["max_score"] = max(diag["max_score"], score)
        if depth > diag["depth"]:
            diag.update(depth=depth, score=score, at=bar["time_key"][11:16])
        threshold = number(spec.get("pin_depth_pct"))
        if threshold is None or threshold <= 0:
            return
        prefix = f"pin:{sym}:{identity}"
        arrival = prefix + ":arrived"
        outside = (f"现在已过 {self.card.main_window[1]} 的主要时段，只当提示。"
                   if not self.card.main_window[0] <= bar_at.strftime("%H:%M") <= self.card.main_window[1] else "")
        if state.get("armed_at") is None and depth >= threshold and score >= spec["min_score"]:
            state["armed_at"] = bar["time_key"]
        if state.get("armed_at") is None:
            return
        if arrival not in self.state["sent"]:
            good = {"仍在开盘价上方": "还在开盘价上方", "高开≥3%": "今天高开 3% 以上", "开盘30分钟后": "开盘已过半小时", "QQQ同期跌>0.4%": "大盘也一起在跌"}
            reasons = "、".join(good[k] for k, v in pts.items() if v)
            hist = ("历史：这种针（跌得够深、条件全齐），30 分钟内先弹 2% 的十次里约七次。" if score >= spec["best_score"]
                    else "历史：这种针等站回均线再接，十次里约五次先涨 2%，约两次先跌破针底，仓位放小。")
            lines = [f"{sym} 插针了：准备接（买 Call）",
                     f"怎么做：先别进。等一分钟线收盘重新站上 20 分钟均线我再提醒「可以接」；进了之后针底 {low:.2f} 跌破就走。",
                     f"为什么：从今天最高点 {peak:.2f} 跌到 {close:.2f}，跌了 {depth:.1%}，超过它平时一天的波动（{threshold:.1%}）"
                     + (f"，而且{reasons}。" if reasons else "。"), hist]
            if spec.get("no_catch_if"):
                lines.append(f"盘前写的不接情况：{spec['no_catch_if']}")
            if outside:
                lines.append(outside)
            self.emit(arrival, at, lines)
            return  # 确认必须来自之后的一根已收盘分钟线。
        period = int(self.card.rules["ma"])
        if bar["time_key"] <= state["armed_at"] or len(bars) < period + 1:
            return
        ma = sum(float(b["close"]) for b in bars[-period:]) / period
        before = sum(float(b["close"]) for b in bars[-period - 1:-1]) / period
        if float(bars[-2]["close"]) <= before and close > ma:
            lines = [f"{sym} 可以接了：站回 20 分钟均线了",
                     f"怎么做：买当天到期、略虚值的 Call，进场价约 {close:.2f}；针底 {low:.2f} 跌破就走，{self.card.flat_by} 前全部了结。",
                     f"为什么：这一分钟收在 {close:.2f}，重新站上了 20 分钟均线 {ma:.2f}。"]
            if spec.get("no_catch_if"):
                lines.append(f"盘前写的不接情况：{spec['no_catch_if']}")
            if outside:
                lines.append(outside)
            self.emit(prefix + ":confirmed", at, lines)



def rth(rows: list[dict], day: date, before: str | None = None) -> list[dict]:
    start, end = f"{day} 09:30:00", f"{day} 16:00:00"
    return sorted((r for r in rows if start <= r["time_key"] <= end and (before is None or r["time_key"] < before)),
                  key=lambda r: r["time_key"])


def replay(card: Card, daily: dict[str, list[dict]], pool: list[str]) -> None:
    series = {}
    for sym in {"QQQ", *pool, *(p["sym"] for p in card.pins)}:
        path = MINUTES / sym / f"{card.day}.json"
        series[sym] = rth(json.loads(path.read_text()), card.day) if path.exists() else []
    previous = {s: float(rows[-1]["close"]) for s, rows in daily.items() if rows}
    vixy, prior = [], []
    with (DATA_DIR / "research" / "vixy_1m.csv").open() as fh:
        for row in csv.DictReader(fh):
            stamp = row["time_key"]
            if "09:30:00" <= stamp[11:19] <= "16:00:00":
                if stamp[:10] == str(card.day):
                    vixy.append({k: v if k == "time_key" else float(v) for k, v in row.items()})
                elif stamp[:10] < str(card.day) and (not prior or stamp > prior[0]):
                    prior = [stamp, float(row["close"])]
    series["VIXY"] = rth(vixy, card.day)
    if "VIXY" not in previous and prior:
        previous["VIXY"] = prior[1]
    if not series["VIXY"] or not series["QQQ"] or any(s not in previous for s in ("QQQ", "VIXY")):
        raise ValueError("回放缺少 VIXY、QQQ 当日分钟线或昨收，无法可靠判断")
    for p in card.pins:
        if p.get("prev_close"):
            previous[p["sym"]] = p["prev_close"]
    events = {}
    for sym, rows in series.items():
        for row in rows:
            events.setdefault(row["time_key"], {})[sym] = row
    seen = {s: [] for s in series}
    totals = {s: [0.0, 0.0] for s in series}
    engine = Engine(card, Telegram(True).send, pool)
    engine.startup(datetime.combine(card.day, datetime.min.time(), ET).replace(hour=9, minute=25))
    for stamp, changes in sorted(events.items()):
        quotes = {}
        for sym, row in changes.items():
            seen[sym].append(row)
            volume = float(row["volume"])
            totals[sym][0] += volume
            totals[sym][1] += (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3 * volume
            quotes[sym] = dict(last_price=row["close"], prev_close_price=previous.get(sym),
                               open_price=series[sym][0]["open"], volume=totals[sym][0], turnover=totals[sym][1])
        engine.step(datetime.fromisoformat(stamp).replace(tzinfo=ET), quotes,
                    {s: rows for s, rows in seen.items() if rows and rows[-1]["time_key"] == stamp})
    if not engine.count:
        print("当日无触发")
    vmax = max(float(b["high"]) for b in series["VIXY"]) / previous["VIXY"] - 1
    qmin = min(float(b["low"]) for b in series["QQQ"]) / previous["QQQ"] - 1
    print(f"{card.day} 回放结束：提醒 {engine.count} 条；当天 VIXY 最大涨幅 {pct(vmax)}；QQQ 最低相对昨收 {pct(qmin)}")
    for p in card.pins:
        d = engine.diagnostics.get(p["sym"])
        detail = (f"最大回落 {d['depth']:.2%}（{d['at']}，当时 {d['score']}/4 分），最高 {d['max_score']}/4 分"
                  if d else "缺少当日分钟线")
        print(f"接针判断 {p['sym']}：阈值 {pct(number(p.get('pin_depth_pct')))}，门槛 {p['min_score']} 分；{detail}")


class LiveFeed:
    def __init__(self, pins: list[dict], pool: list[str]):
        self.ctx = None
        self.subscribed = [f"US.{s}" for s in sorted({"QQQ", *(p["sym"] for p in pins)})]
        self.codes = [f"US.{s}" for s in sorted({"QQQ", "VIXY", *pool, *(p["sym"] for p in pins)})]
        try:
            with socket.create_connection(("127.0.0.1", 11111), timeout=3):
                pass
        except OSError as exc:
            raise RuntimeError(f"无法连接 OpenD 127.0.0.1:11111（{type(exc).__name__}）；请检查 OpenD 与本机网络权限") from None
        from futu import OpenQuoteContext, SubType, KLType, RET_OK
        self.ok, self.ktype = RET_OK, KLType.K_1M
        try:
            self.ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
            ret, _ = self.ctx.subscribe(self.subscribed, [SubType.K_1M])
            if ret != RET_OK:
                raise RuntimeError("OpenD 一分钟线订阅失败，请检查行情权限或订阅额度")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.ctx is not None:
            self.ctx.close()
            self.ctx = None

    def poll(self, at: datetime) -> tuple[dict, dict]:
        quotes, bars = {}, {}
        for offset in range(0, len(self.codes), 400):
            ret, frame = self.ctx.get_market_snapshot(self.codes[offset:offset + 400])
            if ret != self.ok:
                raise RuntimeError("OpenD 快照失败，本轮跳过；请检查行情权限和连接")
            quotes.update({r["code"].removeprefix("US."): r for r in frame.to_dict(orient="records")})
        if any(s not in quotes for s in ("QQQ", "VIXY")):
            raise RuntimeError("OpenD 快照缺少 QQQ 或 VIXY，本轮跳过")
        for code in self.subscribed:
            ret, frame = self.ctx.get_cur_kline(code, 400, self.ktype)
            if ret != self.ok:
                raise RuntimeError(f"{code} 一分钟线读取失败，本轮跳过")
            bars[code.removeprefix("US.")] = rth(frame.to_dict(orient="records"), at.date(),
                                                before=at.strftime("%Y-%m-%d %H:%M:00"))
        return quotes, bars


def live(card: Card, pool: list[str], dry: bool, once: bool) -> None:
    engine = Engine(card, Telegram(dry).send, pool, persistent=not dry)
    feed = None
    vix_at = None
    try:
        while True:
            now = datetime.now(ET)
            hm = now.strftime("%H:%M")
            if not once and (now.date() != card.day or hm >= "16:00"):
                return
            if not once and hm < "09:25":
                time.sleep(min(60, (now.replace(hour=9, minute=25, second=0, microsecond=0) - now).total_seconds()))
                continue
            started = time.monotonic()
            live_state = vixlive_state()          # 本机 VIX 看板：每轮都读（便宜），说法与看板一致
            if live_state:
                engine.update_vix(live_state)
                vix_at = now
            elif vix_at is None or (now - vix_at).total_seconds() >= 300:
                if vix_at is None:
                    engine.update_vix(fetch_vix(card))
                else:
                    q, q3 = cboe_quote("VIX"), cboe_quote("VIX3M")
                    if q and q3:
                        info = vix_stage(q[0], q3[0], None, delayed=True)
                        info.update(vix=q[0], vix3m=q3[0], vix_prev=q[1], source="Cboe 延迟报价", asof=now.isoformat(),
                                    narrowing=engine.vix.get("narrowing"))
                        engine.update_vix(info)
                vix_at = now
            engine.startup(now)
            try:
                if feed is None:
                    feed = LiveFeed(card.pins, pool)
                quotes, bars = feed.poll(now)
                summary = f"一轮快照 {now:%Y-%m-%d %H:%M:%S %Z}：" + "；".join(
                    f"{s} {quotes[s]['last_price']}（相对昨收 {pct(ratio(quotes[s]['last_price'], quotes[s].get('prev_close_price')))}）"
                    for s in ("VIXY", "QQQ"))
                print(summary, flush=True)
                log(summary)
                engine.step(now, quotes, bars)
            except Exception as exc:
                # Telegram 的异常已在其内部吞掉；此处仅输出本模块生成的行情错误。
                message = str(exc) if isinstance(exc, RuntimeError) else f"数据轮询失败（{type(exc).__name__}），本轮跳过"
                log(message)
                print(message, flush=True)
                if once:
                    raise SystemExit(1) from None
                if feed:
                    feed.close()
                    feed = None
            if once:
                return
            remaining = max(0, 60 - (time.monotonic() - started))
            until_close = (now.replace(hour=16, minute=0, second=0, microsecond=0) - datetime.now(ET)).total_seconds()
            time.sleep(max(0, min(remaining, until_close)))
    finally:
        if feed:
            feed.close()


def cmd_alerts(args) -> None:
    try:
        if args.pins is not None and not args.replay:
            raise ValueError("--pins 只用于离线回放")
        if not args.replay and not args.once:
            # 提前启动时到 09:25 才读卡，避免把早晨尚未生成的日内卡判为全天缺失。
            while datetime.now(ET).strftime("%H:%M") < "09:25":
                now = datetime.now(ET)
                time.sleep(min(60, (now.replace(hour=9, minute=25, second=0, microsecond=0) - now).total_seconds()))
        day = date.fromisoformat(args.replay) if args.replay else datetime.now(ET).date()
        card = load_card(day, parse_pins(args.pins))
        daily = daily_context(card)
        pool = symbols_pool()
        if args.replay:
            replay(card, daily, pool)
        else:
            live(card, pool, args.dry_run, args.once)
    except (OSError, ValueError) as exc:
        message = f"盘中提醒无法启动：{type(exc).__name__}；请检查输入文件、参数和状态文件"
        if isinstance(exc, ValueError):
            message += f"（{exc}）"
        print(message)
        log(message)
        raise SystemExit(1) from None
