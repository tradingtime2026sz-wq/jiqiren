"""收盘对账：这张卡今天准不准、我做的和卡对不对得上、盘前写的收盘三验成立没有。

来源：2026-11-10 体系审视——08:50 出卡之前的环节很满，出卡之后没人对答案：日内卡没人打分、
真实成交没对过卡、卡被改写后盘前那版不留底、收盘三验无人回填。river 11-10「搭收盘对账」
「把这个操作安排给合适的齿轮」→ 不新开齿轮，挂在两个现成的环上：
  · 盘中提醒（09:25–16:00）：09:30 把开盘那一刻的卡留底（freeze）；15:54 把卡上那几张期权和
    标的的一分钟线存下来（grab——到期的合约过后富途不一定还认，得趁没到期取）。
  · 期权复盘（16:20）：同步完成交后对账（run），每个交易日都出，没成交也出。

三样产物（~/Codes/reports/收盘对账/）：
  <D>.json / <D>.md   当天对账（.md 第 1 行是结论）
  对账账本.csv          每个进攻候选一行：卡面结果、按卡上进法的结果、方向对没对、复盘红绿灯
  成交对卡.csv          每笔成交一行：卡内 / 反向 / 卡上不进攻 / 卡外（只记输赢和百分比，不记金额）

口径：
  · 卡面结果 = 这张期权开盘第一分钟买、拿到卡上写的了结时刻（默认 15:30）是几倍，期间最高几倍。
    回答「卡选的票和方向对不对」，不含进场手法。
  · 按卡上进法 = 把当天一分钟线喂给盘中提醒同一个 Engine，它喊「可以接」才算进，跌破它当时报的
    止损位就走，否则拿到了结时刻。回答「照卡做是赚是亏」。只有接针 Call 有机器进法；Put 只算卡面结果。
  · 期权价用一分钟收盘价，进出各让半个盘前价差。到期合约取不到价时这一格留空，不估。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DATA_DIR, ET, REPORTS_DIR

CARDS = Path.home() / "Codes" / "reports" / "盘前作战卡"
OUT = Path.home() / "Codes" / "reports" / "收盘对账"
MONITOR_LOG = Path.home() / "Codes" / "ai-trading-team" / "monitor-log.md"
MINUTES = DATA_DIR / "cache" / "futu" / "1m"
FROZEN_FILES = ("odte.json", "odte_judge.json", "odte_review.json", "battle.json", "odds.json", "quotes.json", "日内卡.html")
MIN_HISTORY_QUOTA = 40      # 富途历史 K 线额度（30 天 300 只）剩得比这少，就不为期权合约花额度
JUDGE_MODEL = os.environ.get("RECON_JUDGE_MODEL", "claude-haiku-5-5")
LEDGER_COLS = ["date", "sym", "side", "contract", "card_ask", "open_px", "best_mult", "hold_mult", "rule_result",
               "rule_entry", "rule_exit", "rule_mult", "under_move", "dir_right", "gate0", "bias", "frozen", "revised"]
TRADE_COLS = ["date", "cid", "sym", "side", "label", "result", "ret"]


def log(message: str) -> None:
    """Append one line to data/logs/recon.log."""
    d = DATA_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "recon.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now(ET):%Y-%m-%d %H:%M:%S} {message}\n")


def _sha(path: Path) -> str | None:
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.exists() else None


def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------- 留底 ----------------

def freeze(day: date) -> bool | None:
    """把开盘那一刻的卡留一份到 <D>/frozen/。已留过返回 False；当天没有卡返回 None。只留第一次。"""
    src, dest = CARDS / str(day), CARDS / str(day) / "frozen"
    if (dest / "meta.json").exists():
        return False
    if not (src / "odte.json").exists():
        return None
    dest.mkdir(parents=True, exist_ok=True)
    kept = []
    for name in FROZEN_FILES:
        if (src / name).exists():
            shutil.copy2(src / name, dest / name)
            kept.append(name)
    meta = {"frozen_at": datetime.now(ET).isoformat(timespec="seconds"), "files": kept,
            "odte_sha1": _sha(dest / "odte.json")}
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{day} 卡已留底：{'、'.join(kept)}")
    return True


def card_dir(day: date) -> tuple[Path, bool]:
    """对账用哪一版卡：有留底用留底，没有用现在盘上那版（并标 frozen=False）。"""
    frozen = CARDS / str(day) / "frozen"
    ok = (frozen / "meta.json").exists() and (frozen / "odte.json").exists()   # meta 最后写：没有它说明留底没留完
    return (frozen, True) if ok else (CARDS / str(day), False)


def option_code(sym: str, expiry: str, kind: str, strike: float) -> str:
    """富途期权代码：US.QQQ261109C662000。"""
    return f"US.{sym}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{'C' if kind.upper().startswith('C') else 'P'}{int(round(float(strike) * 1000))}"


def attacks(src: Path) -> list[dict]:
    """卡上的进攻候选，各带它那一侧的期权腿（按离平值由近到远）。"""
    odte = _load(src / "odte.json", {}) or {}
    odds = {r.get("sym"): r for r in (_load(src / "odds.json", {}) or {}).get("rows", []) if isinstance(r, dict)}
    out = []
    for a in odte.get("attack") or []:
        sym, side = a.get("sym"), a.get("side")
        if not sym or side not in ("Call", "Put"):
            continue
        row = odds.get(sym) or {}
        legs = []
        for leg in row.get("legs") or []:
            if str(leg.get("kind", "")).upper().startswith(side[0].upper()) and row.get("expiry") and leg.get("strike"):
                legs.append({"code": option_code(sym, row["expiry"], leg["kind"], leg["strike"]),
                             "strike": float(leg["strike"]), "ask": leg.get("ask"), "bid": leg.get("bid"),
                             "spread_pct": leg.get("spread_pct"), "expiry": row["expiry"]})
        out.append({"sym": sym, "side": side, "tier": a.get("tier"), "on_main": a.get("on_main"),
                    "no_catch_if": a.get("no_catch_if"), "legs": legs})
    return out


# ---------------- 取数 ----------------

def _rows(frame, day: date) -> list[dict]:
    start, end = f"{day} 09:30:00", f"{day} 16:00:00"
    keep = ("time_key", "open", "high", "low", "close", "volume", "turnover")
    rows = [{k: r[k] for k in keep if k in r} for r in frame.to_dict(orient="records")]
    return sorted((r for r in rows if start < r["time_key"] <= end), key=lambda r: r["time_key"])


def _opend_up() -> bool:
    """OpenD 端口通不通。富途的连接对象在 OpenD 不在时会一直重连不返回，所以开连接前先探一下。"""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 11111), timeout=3):
            return True
    except OSError:
        return False


def grab(day: date) -> bool:
    """收盘前（15:54 起）把卡上用到的期权和标的的一分钟线存到 <D>/frozen/。只做一次。

    自己开一条行情连接（盘中提醒把它放在后台线程里跑，不占提醒那一轮）；走订阅取当天 K 线，不花历史额度；
    每个进攻候选只取对账用到的那一张（离平值最近的），取完关连接，订阅额度自动还回去。
    """
    src, _ = card_dir(day)
    dest = CARDS / str(day) / "frozen"
    if (dest / "option_1m.json").exists():
        return False
    if not _opend_up():
        log(f"{day} 收盘前取数：OpenD 不在线，没取")
        return False
    from futu import KLType, RET_OK, SubType
    att = attacks(src)
    opt = [a["legs"][0]["code"] for a in att if a["legs"]]
    und = sorted({"QQQ", "SOXX", *(a["sym"] for a in att)})
    dest.mkdir(parents=True, exist_ok=True)
    ctx = _futu()
    try:
        return _grab_with(ctx, day, dest, opt, und, KLType, RET_OK, SubType)
    finally:
        ctx.close()


def _grab_with(ctx, day: date, dest: Path, opt: list[str], und: list[str], KLType, RET_OK, SubType) -> bool:
    """grab 的取数部分（连接由调用方开关）。"""
    got: dict[str, dict] = {"option": {}, "under": {}}
    for group, codes in (("option", opt), ("under", [f"US.{s}" for s in und])):
        if not codes:
            continue
        ret, msg = ctx.subscribe(codes, [SubType.K_1M])
        if ret != RET_OK:
            log(f"{day} 收盘前取数：{group} 订阅没成（{str(msg)[:80]}），这一组留空")
            continue
        for code in codes:
            ret, frame = ctx.get_cur_kline(code, 1000, KLType.K_1M)
            if ret == RET_OK and len(frame):
                got[group][code] = _rows(frame, day)
    (dest / "option_1m.json").write_text(json.dumps(got["option"], ensure_ascii=False), encoding="utf-8")
    (dest / "under_1m.json").write_text(json.dumps(got["under"], ensure_ascii=False), encoding="utf-8")
    log(f"{day} 收盘前取数：期权 {len(got['option'])}/{len(opt)} 张，标的 {len(got['under'])}/{len(und)} 只")
    return True


def _futu():
    from futu import OpenQuoteContext
    return OpenQuoteContext(host="127.0.0.1", port=11111)


def _history(ctx, code: str, day: date) -> list[dict]:
    from futu import AuType, KLType, RET_OK
    ret, frame, _ = ctx.request_history_kline(code, start=str(day), end=str(day), ktype=KLType.K_1M,
                                              autype=AuType.NONE, max_count=1000)
    return _rows(frame, day) if ret == RET_OK and len(frame) else []


def load_bars(day: date, att: list[dict], allow_history: bool) -> tuple[dict, dict, list[str]]:
    """返回 (标的一分钟线, 期权一分钟线, 说明)。先用收盘前存的，缺的再补：标的走历史 K 线，
    期权只在允许且额度够时补每个进攻候选最近平值那一张。"""
    dest = CARDS / str(day) / "frozen"
    under = {k.removeprefix("US."): v for k, v in (_load(dest / "under_1m.json", {}) or {}).items()}
    option = dict(_load(dest / "option_1m.json", {}) or {})
    notes: list[str] = []
    full = lambda rows: bool(rows) and rows[-1]["time_key"][11:16] >= "15:59"   # noqa: E731 - 15:54 存的那份不到收盘，要补齐
    need_u = [s for s in sorted({"QQQ", "SOXX", *(a["sym"] for a in att)}) if not full(under.get(s))]
    need_o = [a["legs"][0]["code"] for a in att if a["legs"] and not option.get(a["legs"][0]["code"])]
    for s in list(need_u):
        cached = _load(MINUTES / s / f"{day}.json")
        if cached:
            start, end = f"{day} 09:30:00", f"{day} 16:00:00"
            rows = sorted((r for r in cached if start < r["time_key"] <= end), key=lambda r: r["time_key"])
            if full(rows):
                under[s] = rows
                need_u.remove(s)
    if not need_u and not need_o:
        return under, option, notes
    if not _opend_up():
        notes.append("富途连不上，缺的分钟线没补（方向和三验可能只算到 15:53）")
        return under, option, notes
    ctx = _futu()
    try:
        today = datetime.now(ET).date() == day
        if need_u and today:   # 当天的标的走订阅取，不花历史额度
            from futu import KLType, RET_OK, SubType
            codes = [f"US.{s}" for s in need_u]
            ret, _msg = ctx.subscribe(codes, [SubType.K_1M])
            for code in codes if ret == RET_OK else []:
                ret2, frame = ctx.get_cur_kline(code, 1000, KLType.K_1M)
                rows = _rows(frame, day) if ret2 == RET_OK and len(frame) else []
                if full(rows):
                    under[code.removeprefix("US.")] = rows
                    need_u.remove(code.removeprefix("US."))
        for s in need_u:
            rows = _history(ctx, f"US.{s}", day)
            if rows:
                under[s] = rows
        short = [s for s in sorted(under) if under[s] and not full(under[s])]
        if short:
            notes.append(f"{'、'.join(short)} 的分钟线没到收盘，方向和三验按取到的最后一分钟算")
        if need_o:
            from futu import RET_OK
            ret, quota = ctx.get_history_kl_quota(get_detail=False)
            remain = quota[1] if ret == RET_OK else 0
            if not allow_history:
                notes.append("收盘前没存到期权分钟线，期权倍数留空")
            elif remain - len(need_o) < MIN_HISTORY_QUOTA:
                notes.append(f"富途历史额度只剩 {remain}，不为期权合约花额度，期权倍数留空")
            else:
                for code in need_o:
                    option[code] = _history(ctx, code, day)
                notes.append("期权分钟线是事后补取的（走富途历史额度）")
    finally:
        ctx.close()
    return under, option, notes


# ---------------- 算 ----------------

def _minutes(hm: str) -> int:
    return int(hm[:2]) * 60 + int(hm[3:5])


def _at(rows: list[dict], hm: str) -> dict | None:
    """不晚于 hm 的最后一根。"""
    hit = [r for r in rows if r["time_key"][11:16] <= hm]
    return hit[-1] if hit else None


def face_result(rows: list[dict], flat_by: str) -> dict:
    """卡面结果：开盘第一分钟买，拿到了结时刻几倍、期间最高几倍。"""
    near = [r for r in rows if _minutes(r["time_key"][11:16]) >= _minutes(flat_by) - 5 and r["time_key"][11:16] <= flat_by]
    if not rows or rows[0]["time_key"][11:16] > "09:35" or not near:
        return {}   # 开盘那几分钟或了结时刻附近没有价：不估，留空
    first = rows[0]
    open_px = float(first["open"]) or float(first["close"])
    window = [r for r in rows if r["time_key"][11:16] <= flat_by] or rows
    hold = float(window[-1]["close"])
    best_row = max(window, key=lambda r: float(r["high"]))
    return {"open_px": round(open_px, 2), "hold_px": round(hold, 2), "best_px": round(float(best_row["high"]), 2),
            "best_at": best_row["time_key"][11:16], "hold_mult": round(hold / open_px, 2) if open_px else None,
            "best_mult": round(float(best_row["high"]) / open_px, 2) if open_px else None}


def rule_replay(day: date, src: Path, under: dict) -> dict:
    """把当天一分钟线喂给盘中提醒的 Engine，看它什么时候会喊「准备接 / 可以接 / 破位不接」。"""
    from .alerts import Engine, load_card
    card = load_card(day, source=src / "odte.json")
    events: list[dict] = []

    def capture(at: datetime, message: str) -> bool:
        events.append({"at": at.strftime("%H:%M"), "text": message})
        return True

    engine = Engine(card, capture, [])
    stamps = sorted({r["time_key"] for s in {"QQQ", *(p["sym"] for p in card.pins)} for r in under.get(s, [])})
    seen: dict[str, list[dict]] = {}
    marks: dict[str, dict] = {}
    for stamp in stamps:
        for s in under:
            seen.setdefault(s, []).extend(r for r in under[s] if r["time_key"] == stamp)
        # 实盘每轮只看已经收完的分钟线：time_key 是这一分钟的结束时刻，所以这根线要到下一分钟那一轮才被看到
        at = datetime.fromisoformat(stamp).replace(tzinfo=ET) + timedelta(minutes=1)
        hm = at.strftime("%H:%M")
        if not "09:30" <= hm <= card.flat_by:
            continue
        before = len(engine.state["sent"])
        for pin in card.pins:
            engine.pin(at, pin, {s: rows for s, rows in seen.items() if rows and rows[-1]["time_key"] == stamp})
        for key in engine.state["sent"][before:]:
            kind = key.rsplit(":", 1)[-1]
            sym = key.split(":")[1]
            m = marks.setdefault(sym, {})
            if kind in ("arrived", "confirmed", "broke") and kind not in m:
                m[kind] = hm
                if kind == "confirmed":
                    bars = seen[sym]
                    if key.startswith("lvl:"):
                        stop = float(engine.state["pins"][sym].get("pull_low") or bars[-1]["low"])
                    else:
                        peak_i = max(range(len(bars)), key=lambda i: float(bars[i]["high"]))
                        stop = min(float(b["low"]) for b in bars[peak_i:])
                    m.update(entry_px=float(bars[-1]["close"]), stop=stop, entry_stamp=stamp)
    out = {}
    for pin in card.pins:
        sym, m = pin["sym"], marks.get(pin["sym"], {})
        rows = under.get(sym, [])
        res = {"mode": (pin.get("entry") or {}).get("mode") or "pin", **{k: m.get(k) for k in ("arrived", "confirmed", "broke")}}
        if "confirmed" in m:
            after = [r for r in rows if r["time_key"] > m["entry_stamp"] and r["time_key"][11:16] <= card.flat_by]
            stopped = next((r for r in after if float(r["low"]) < m["stop"]), None)
            last = stopped or (after[-1] if after and _minutes(card.flat_by) - _minutes(after[-1]["time_key"][11:16]) <= 5 else None)
            res.update(result="进了、止损走" if stopped else "进了、拿到了结", entry_px=round(m["entry_px"], 2),
                       stop=round(m["stop"], 2), exit=last["time_key"][11:16] if last else None,
                       exit_px=round(float(last["close"]), 2) if last else None,
                       in_main_window=card.main_window[0] <= m["confirmed"] <= card.main_window[1])
        elif "broke" in m:
            res["result"] = "破位不接"
        elif "arrived" in m:
            res["result"] = "到位了、没等到确认"
        else:
            res["result"] = "没到进场位"
        out[sym] = res
    return {"flat_by": card.flat_by, "pins": out, "events": events}


def option_rule_mult(rows: list[dict], entry_hm: str, exit_hm: str, spread_pct) -> float | None:
    """按卡上进法的期权倍数：进出都用那一分钟的收盘价，各让半个盘前价差。"""
    e, x = _at(rows, entry_hm), _at(rows, exit_hm)
    if not e or not x or e["time_key"][11:16] != entry_hm or _minutes(exit_hm) - _minutes(x["time_key"][11:16]) > 5 \
            or x["time_key"] <= e["time_key"]:
        return None   # 进场那一分钟没价，或出场时刻附近 5 分钟没价：不拿旧价顶
    half = min(float(spread_pct or 0.04), 0.3) / 2
    buy, sell = float(e["close"]) * (1 + half), float(x["close"]) * (1 - half)
    return round(sell / buy, 2) if buy > 0 else None


def gate0(day: date) -> dict:
    """每日复盘对「上一个收盘」判的红黄绿灯（只读 monitor-log 最上面那条）。对不上日期就写未知。"""
    try:
        text = MONITOR_LOG.read_text(encoding="utf-8")
    except OSError:
        return {"color": "未知", "why": "读不到每日复盘"}
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    m = re.search(rf"^##+ [^\n]*覆盖{prev}美股收盘[^\n]*$(.*?)(?=^##+ |\Z)", text, re.M | re.S)
    if not m:
        return {"color": "未知", "why": f"每日复盘里没有覆盖 {prev} 收盘的那一条"}
    g = re.search(r"【Gate 0】(.{0,60})", m.group(1))
    if not g:
        return {"color": "未知", "why": "那条复盘没写 Gate 0"}
    line = g.group(1)
    color = "红" if re.search(r"RED|红灯", line) else "黄" if re.search(r"YELLOW|黄灯", line) else \
        "绿" if re.search(r"GREEN|绿灯", line) else "未知"
    return {"color": color, "why": line.strip(" *｜|；;")[:50], "covers": str(prev)}


def trades(day: date, att: list[dict], card_syms: set[str], review: str | None = None) -> dict:
    """当天开的仓，和卡对一遍。只记输赢与百分比，不记金额。

    review：这次每日复盘的结局——ok（同步和复盘都成了）/ sync_failed / review_failed；None 表示手动对账，
    这时看复盘报告文件是不是当天收盘后写的。
    """
    files = sorted(REPORTS_DIR.glob("期权复盘-*.json"), key=lambda p: p.stat().st_mtime)
    if review is None:
        state = _load(DATA_DIR / "raw" / "auto_state.json", {}) or {}
        seen_at = str(state.get("reported_at") or "")[:16]
        fresh = bool(seen_at >= f"{day}T16:00" or (_load(DATA_DIR / "raw" / "meta.json", {}) or {}).get("synced_at", "")[:16] >= f"{day}T16:00")
    else:
        fresh = review == "ok"
    synced = review or "manual"
    recs = (_load(files[-1], {}) or {}).get("recs", []) if files else []
    side_of = {"看涨": "Call", "看跌": "Put"}
    want = {(a["sym"], a["side"]) for a in att}
    att_syms = {a["sym"] for a in att}
    rows = []
    for r in recs:
        if str(r.get("open_ts", ""))[:10] != str(day):
            continue
        sym, side = r.get("underlying"), side_of.get(r.get("bias"), r.get("bias"))
        base = {"GOOG": "GOOGL"}.get(sym, sym)
        if (base, side) in want or (sym, side) in want:
            label = "卡内"
        elif (base in att_syms or sym in att_syms) and side in ("Call", "Put"):
            label = "反向"
        elif base in att_syms or sym in att_syms:
            label = "待核"   # 跨式这类不是单边的：机器不判它和卡是不是一个方向
        elif base in card_syms or sym in card_syms:
            label = "卡上不进攻"
        else:
            label = "卡外"
        pnl = r.get("pnl")
        result = "持仓中" if r.get("status") != "closed" else "赚" if (pnl or 0) > 0 else "亏"
        rows.append({"cid": r.get("cid"), "sym": sym, "side": side, "label": label, "result": result,
                     "ret": round(float(r["ret"]), 3) if isinstance(r.get("ret"), (int, float)) else None})
    return {"synced": fresh, "review": synced, "rows": rows}


def snapshot_facts(day: date, syms: list[str], under: dict) -> dict:
    """三验要用的收盘事实：每只票今天开高低收和昨收。标的分钟线里有的直接算，别的拿快照；昨收取卡上盘前报价那份。"""
    facts = {}
    src, _ = card_dir(day)
    quotes = _load(src / "quotes.json") or _load(CARDS / str(day) / "quotes.json") or {}
    for s in syms:
        rows = under.get(s) or []
        if rows:
            facts[s] = {"open": float(rows[0]["open"]), "high": max(float(r["high"]) for r in rows),
                        "low": min(float(r["low"]) for r in rows), "close": float(rows[-1]["close"]),
                        "prev_close": (quotes.get(s) or {}).get("prev_close")}
    missing = [s for s in syms if s not in facts]
    if missing and datetime.now(ET).date() == day:
        try:
            from futu import RET_OK
            ctx = _futu()
            try:
                ret, frame = ctx.get_market_snapshot([f"US.{s}" for s in missing])
                if ret == RET_OK:
                    for r in frame.to_dict(orient="records"):
                        facts[r["code"].removeprefix("US.")] = {
                            "open": r.get("open_price"), "high": r.get("high_price"), "low": r.get("low_price"),
                            "close": r.get("last_price"), "prev_close": r.get("prev_close_price")}
            finally:
                ctx.close()
        except Exception as exc:  # noqa: BLE001 - 三验缺事实就判不了，不影响其余
            log(f"{day} 三验快照没取到：{type(exc).__name__}")
    return facts


OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}
ALIAS = {"费半": "SOXX", "纳指": "QQQ", "大盘": "QQQ", "标普": "SPY"}


def eval_rules(rules: list[dict], facts: dict) -> tuple[str, str]:
    """一条验证拆成的若干机器式（全部成立才算成立）。式子：{sym, field: close|low|high|open, op, ref: 数字|prev_close|open}。"""
    parts, verdict = [], "成立"
    for r in rules:
        f = facts.get(r.get("sym")) or {}
        left = f.get(r.get("field", "close"))
        ref = r.get("ref")
        right = f.get(ref) if isinstance(ref, str) else ref
        op = OPS.get(r.get("op"))
        if left is None or right is None or op is None:
            return "判不了", f"{r.get('sym')} 缺数"
        ok = op(float(left), float(right))
        parts.append(f"{r['sym']} {float(left):.2f} {'≥' if '>' in r['op'] else '≤'} {float(right):.2f}" + ("" if ok else "（没达到）"))
        if not ok:
            verdict = "不成立"
    return verdict, "；".join(parts)[:60]


def parse_check(text: str, extra: dict) -> list[dict] | None:
    """没给机器式时，从常见写法里认：守住 X / 收红 / 收绿 / 站上 X / 不创新低（只认得出给了前低的）。认不出返回 None。"""
    t = text
    for k, v in ALIAS.items():
        t = t.replace(k, f" {v} ")
    syms = re.findall(r"\b[A-Z]{2,5}\b", t)
    if not syms:
        return None
    num = re.search(r"\$?\s*(\d{2,5}(?:\.\d+)?)", t)
    rules: list[dict] = []
    rest = t
    for pat, make in (
        (r"守住|不破|守在|站上|收复|站回", lambda s: {"sym": s, "field": "close", "op": ">=", "ref": float(num.group(1))} if num else None),
        (r"收红|收涨|翻红", lambda s: {"sym": s, "field": "close", "op": ">", "ref": "prev_close"}),
        (r"收绿|收跌|翻绿", lambda s: {"sym": s, "field": "close", "op": "<", "ref": "prev_close"}),
        (r"不创新低", lambda s: {"sym": s, "field": "low", "op": ">=", "ref": float(extra["lvl_20d_low_tomorrow"])}
            if s == "QQQ" and extra.get("lvl_20d_low_tomorrow") else None),
    ):
        if re.search(pat, rest):
            made = [make(s) for s in syms]
            if any(m is None for m in made):
                return None
            rules += made
            rest = re.sub(pat, "", rest)
    # 一句里只要还剩认不出的条件（如「承接住」「盘前低点」），整条就判不了——不能只验一半就说成立
    rest = re.sub(r"[A-Z]{2,5}|\$?\s*\d+(?:\.\d+)?|收盘|当天|今天|日低|[都均和与且并、，,；; ]", "", rest)
    return rules if rules and not rest.strip() else None


def machine_checks(checks: list[str], rules, facts: dict, extra: dict) -> list[dict]:
    """先用审稿员给的机器式（checks3_rule，与 checks3 一一对应），没有就从句子里认，认不出标「判不了」。"""
    out = []
    for i, c in enumerate(checks):
        rule = rules[i] if isinstance(rules, list) and i < len(rules) and rules[i] else parse_check(c, extra)
        if isinstance(rule, dict):
            rule = [rule]
        if not rule:
            out.append({"check": c, "verdict": "判不了", "why": "这句话机器认不出怎么验（审稿时没给机器式）"})
            continue
        verdict, why = eval_rules(rule, facts)
        out.append({"check": c, "verdict": verdict, "why": why})
    return out


def judge_checks(checks: list[str], facts: dict, extra: dict) -> list[dict]:
    """盘前写的收盘三验，逐条对事实判 成立 / 不成立 / 判不了。调本机 claude（小模型，一轮，不用工具）。"""
    if not checks:
        return []
    blank = [{"check": c, "verdict": "未判", "why": ""} for c in checks]
    from .leaders import _claude_bin
    cb = _claude_bin()
    if not cb:
        return blank
    prompt = (
        "下面是盘前写好的「收盘怎么验」几条，和今天收盘后的事实。逐条判断：成立、不成立、还是判不了（事实不够）。\n"
        "说明：费半用 SOXX 代表；「收红」指收盘高于昨收（美股口径：涨=红在这里指上涨）；「承接住」按收盘不低于开盘算；"
        "「不创新低」按今天最低没有低于给出的前低算，没给前低就判不了。只用给的数字，不要猜。\n"
        f"几条：{json.dumps(checks, ensure_ascii=False)}\n"
        f"今天各票（开/高/低/收/昨收）：{json.dumps(facts, ensure_ascii=False)}\n"
        f"其他参考位：{json.dumps(extra, ensure_ascii=False)}\n"
        '只输出一行 JSON 数组，不要别的字：[{"check":"原句","verdict":"成立|不成立|判不了","why":"不超过30字，带数字"}]\n'
        "不要调用任何工具。")
    try:
        r = subprocess.run([cb, "-p", "--model", JUDGE_MODEL, "--max-turns", "1", "--no-session-persistence",
                            "--output-format", "text", prompt], capture_output=True, text=True, timeout=120,
                           cwd=str(Path.home()), env={**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
        m = re.search(r"\[.*\]", r.stdout, re.S)
        rows = json.loads(m.group(0)) if r.returncode == 0 and m else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        rows = None
    if not isinstance(rows, list) or len(rows) != len(checks):
        log("三验没判成（claude 调用失败或输出不对），记未判")
        return blank
    return [{"check": c, "verdict": str(x.get("verdict", "未判"))[:6], "why": str(x.get("why", ""))[:60]}
            for c, x in zip(checks, rows)]


# ---------------- 出 ----------------

def _upsert(path: Path, cols: list[str], day: date, rows: list[dict]) -> None:
    """账本按日期整体替换，重跑不重复。"""
    old = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            old = [r for r in csv.DictReader(fh) if r.get("date") != str(day)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sorted(old + rows, key=lambda r: (r.get("date", ""), r.get("sym", ""), r.get("cid", ""))):
            w.writerow(r)


def _x(v) -> str:
    return f"{v:.1f} 倍" if isinstance(v, (int, float)) else "没取到价"


def summarize(rec: dict) -> list[str]:
    """三行人话：卡 / 我 / 三验与灯。"""
    cards = []
    for a in rec["attacks"]:
        f, rule = a.get("face") or {}, a.get("rule") or {}
        name = f"{a['sym']} {a['side']}"
        s = f"{name}：开盘买拿到 {rec['flat_by']} 是 {_x(f.get('hold_mult'))}，最高 {_x(f.get('best_mult'))}" if f else \
            f"{name}：期权价没取到，标的开盘到收盘 {a['under_move']:+.1%}" if a.get("under_move") is not None else f"{name}：没取到行情"
        if rule:
            s += f"；按卡上进法「{rule['result']}」" + (f"（{rule['confirmed']} 进，{_x(a.get('rule_mult'))}）" if rule.get("confirmed") else "")
        cards.append(s)
    line1 = "卡：" + ("；".join(cards) if cards else "今天没有进攻候选")
    t = rec["trades"]
    if not t["synced"]:
        line2 = "你：今天的成交没对上——" + {"sync_failed": "老虎订单没同步到", "review_failed": "成交复盘没出完"}.get(
            t.get("review"), "还没看到今天收盘后的成交复盘")
    elif not t["rows"]:
        line2 = "你：今天没开新仓"
    else:
        by = {}
        for r in t["rows"]:
            by.setdefault(r["label"], []).append(r["result"])
        line2 = "你：开了 " + f"{len(t['rows'])} 笔——" + "、".join(
            f"{k} {len(v)}（" + " ".join(f"{x}{v.count(x)}" for x in ("赚", "亏", "持仓中") if v.count(x)) + "）" for k, v in by.items())
    ch = rec["checks"]
    ok = sum(c["verdict"] == "成立" for c in ch)
    bad = [c["check"] for c in ch if c["verdict"] == "不成立"]
    unk = sum(c["verdict"] not in ("成立", "不成立") for c in ch)
    line3 = (f"三验：{ok}/{len(ch)} 成立" + (f"，没成立的是「{'」「'.join(bad)}」" if bad else "")
             + (f"，{unk} 条机器判不了" if unk else "") if ch else "三验：卡上没写") \
        + f"；每日复盘给昨天收盘的灯：{rec['gate0']['color']}"
    return [line1, line2, line3]


def run(day: date, allow_history: bool = False, judge: bool = True, review: str | None = None) -> dict | None:
    """对账主流程。没有卡写上游缺文件返回 None。"""
    OUT.mkdir(parents=True, exist_ok=True)
    src, frozen = card_dir(day)
    odte = _load(src / "odte.json")
    if not odte:
        (OUT / f"{day}-上游缺.md").write_text(f"{day} 没有日内卡（{src}/odte.json 不在），收盘对账没法做。\n", encoding="utf-8")
        log(f"{day} 没有日内卡，写了上游缺")
        return None
    live_sha = _sha(CARDS / str(day) / "odte.json")
    revised = bool(frozen and live_sha and live_sha != _sha(src / "odte.json"))
    live_att = {(a["sym"], a["side"]) for a in attacks(CARDS / str(day))} if revised else None
    att = attacks(src)
    under, option, notes = load_bars(day, att, allow_history)
    try:
        replay = rule_replay(day, src, under)
    except Exception as exc:  # noqa: BLE001 - 进法回放坏了不挡卡面结果
        log(f"{day} 进法回放没跑成：{type(exc).__name__}: {exc}")
        notes.append("按卡上进法这一栏没算成")
        replay = {"flat_by": "15:30", "pins": {}, "events": []}
    flat_by = replay["flat_by"]
    g0 = gate0(day)
    out_att = []
    for a in att:
        leg = a["legs"][0] if a["legs"] else None
        urows = under.get(a["sym"]) or []
        move = (float(urows[-1]["close"]) / float(urows[0]["open"]) - 1) if urows else None
        item = {"sym": a["sym"], "side": a["side"], "on_main": a.get("on_main"), "no_catch_if": a.get("no_catch_if"),
                "contract": leg["code"].removeprefix("US.") if leg else None, "card_ask": leg.get("ask") if leg else None,
                "under_move": round(move, 4) if move is not None else None,
                "dir_right": None if move is None else (move > 0) == (a["side"] == "Call")}
        orows = option.get(leg["code"]) if leg else None
        if orows:
            item["face"] = face_result(orows, flat_by)
        rule = replay["pins"].get(a["sym"]) if a["side"] == "Call" else None
        if rule:
            item["rule"] = rule
            if rule.get("confirmed") and orows:
                item["rule_mult"] = option_rule_mult(orows, rule["confirmed"], rule.get("exit") or flat_by,
                                                     leg.get("spread_pct"))
        out_att.append(item)
    card_syms = {r.get("sym") for r in odte.get("rows") or [] if isinstance(r, dict)} | set(odte.get("universe") or [])
    tr = trades(day, att, {s for s in card_syms if isinstance(s, str)}, review)
    panel = ((odte.get("review") or {}).get("panel") or {})
    checks = [c for c in panel.get("checks3") or [] if isinstance(c, str)]
    tick = sorted({"QQQ", "SOXX", *(a["sym"] for a in att), *re.findall(r"\b[A-Z]{2,5}\b", " ".join(checks))})
    facts = snapshot_facts(day, tick, under) if checks else {}
    w = odte.get("weather") or {}
    extra = {k: w.get(k) for k in ("qqq_close", "lvl_20d_low_tomorrow", "ma200") if w.get(k) is not None}
    rules = ((_load(src / "odte_review.json", {}) or {}).get("panel") or {}).get("checks3_rule")
    verdicts = machine_checks(checks, rules, facts, extra)
    if judge and any(v["verdict"] == "判不了" for v in verdicts):   # 机器认不出的那几条，试一次本机 claude；不通就保持「判不了」
        asked = judge_checks([v["check"] for v in verdicts if v["verdict"] == "判不了"], facts, extra)
        it = iter(asked)
        merged = []
        for v in verdicts:
            if v["verdict"] == "判不了":
                j = next(it)
                v = j if j["verdict"] in ("成立", "不成立") else v   # claude 没判出来就保留机器那句「为什么判不了」
            merged.append(v)
        verdicts = merged
    rec = {"date": str(day), "made_at": datetime.now(ET).isoformat(timespec="minutes"), "frozen": frozen,
           "revised_after_open": revised,
           "revised_attacks": sorted(f"{s} {d}" for s, d in live_att) if live_att is not None else None,
           "bias": odte.get("bias"), "flat_by": flat_by, "gate0": g0, "attacks": out_att, "trades": tr,
           "checks": verdicts, "weak": panel.get("weak"), "alerts_would_fire": replay["events"], "notes": notes}
    lines = summarize(rec)
    rec["summary"] = lines
    (OUT / f"{day}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [lines[0], "", f"# 收盘对账 {day}", "", *(f"- {x}" for x in lines[1:]), ""]
    if not frozen:
        md.append("- 注意：今天开盘时没留到底，用的是现在盘上那版卡（可能是改过的）。")
    if revised:
        md.append(f"- 卡在开盘后被改过：现在盘上的进攻是 {'、'.join(rec['revised_attacks']) or '空'}；对账按开盘那版。")
    md += [f"- {n}" for n in notes]
    (OUT / f"{day}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    _upsert(OUT / "对账账本.csv", LEDGER_COLS, day, [{
        "date": str(day), "sym": a["sym"], "side": a["side"], "contract": a.get("contract"), "card_ask": a.get("card_ask"),
        "open_px": (a.get("face") or {}).get("open_px"), "best_mult": (a.get("face") or {}).get("best_mult"),
        "hold_mult": (a.get("face") or {}).get("hold_mult"), "rule_result": (a.get("rule") or {}).get("result"),
        "rule_entry": (a.get("rule") or {}).get("confirmed"), "rule_exit": (a.get("rule") or {}).get("exit"),
        "rule_mult": a.get("rule_mult"), "under_move": a.get("under_move"), "dir_right": a.get("dir_right"),
        "gate0": g0["color"], "bias": rec["bias"], "frozen": frozen, "revised": revised} for a in out_att])
    if tr["synced"] or tr["rows"]:
        _upsert(OUT / "成交对卡.csv", TRADE_COLS, day, [{"date": str(day), **r} for r in tr["rows"]])
    stale = OUT / f"{day}-上游缺.md"
    if stale.exists():
        stale.unlink()
    log(f"{day} 对账完成：" + " | ".join(lines))
    return rec


def first_notice(day: date) -> bool:
    """这一天的三行结论发过没有：没发过返回 True 并记下（重跑不重复发私聊）。"""
    path = OUT / ".sent.json"
    sent = _load(path, {}) or {}
    if sent.get(str(day)):
        return False
    sent[str(day)] = datetime.now(ET).isoformat(timespec="minutes")
    path.write_text(json.dumps(sent, ensure_ascii=False, indent=1), encoding="utf-8")
    return True
