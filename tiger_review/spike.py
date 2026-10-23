"""插针读数：和回测（research/spike_backtest.py）同一套定义，盘中查询与复盘卡片共用。

一根针 = 热点票在盘中从最近 30 分钟高点回落，回落深度按日均振幅（ATR14）衡量，
在某一分钟创出新低。进场质量按回测里两期都成立的四条打分：
  仍在当天开盘价上方 2 分；高开 ≥3% 1 分；开盘 30 分钟以后 1 分；QQQ 同期也跌 >0.4% 1 分。
统计只在「热点日 + 回落 ≥1 倍日均振幅」上有区分度；浅、中等回落的针接近五五开。
"""
from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np

from .config import DATA_DIR, ET
from .market import Prices

LOOKUP = DATA_DIR / "research" / "spike_lookup.json"


def _rth(p: Prices, sym: str, d: date) -> list[dict]:
    return [x for x in p.minute(sym, d) if f"{d} 09:31:00" <= x["time_key"] <= f"{d} 16:00:00"]


def depth_bucket(x: float) -> str:
    return "深回落" if x >= 1.0 else "中等回落" if x >= 0.5 else "浅回落"


def _inv_bar(b: dict) -> dict:
    return {**b, "open": 1 / b["open"], "close": 1 / b["close"], "high": 1 / b["low"], "low": 1 / b["high"]}


def reading(p: Prices, sym: str, ts: datetime, direction: str = "down") -> dict | None:
    """ts 这一分钟的读数。direction="down" 读向下插针（看涨单），"up" 读向上冲高（看跌单）。

    冲高的计算把价格取倒数，冲高就成了插针，和镜像回测同一口径；输出的价格再换回原值。
    """
    up = direction == "up"
    d = ts.astimezone(ET).date()
    p.ensure_daily(sym, date(d.year - 1, d.month, 1) if d.month > 1 else date(d.year - 1, 1, 1), d)
    daily = [r for r in p.daily(sym) if r["time_key"][:10] < d.isoformat()]
    if up:
        daily = [_inv_bar(r) for r in daily]
    if len(daily) < 16:
        return None
    rng = [(daily[i]["high"] - daily[i]["low"]) / daily[i - 1]["close"] for i in range(len(daily) - 14, len(daily))]
    atr = float(np.mean(rng))
    prev_close = float(daily[-1]["close"])
    vol20 = float(np.mean([r["volume"] for r in daily[-20:]]))
    bars = _rth(p, sym, d)
    if up:
        bars = [_inv_bar(b) for b in bars]
    stamp = ts.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")
    idx = [i for i, b in enumerate(bars) if b["time_key"] <= stamp]
    if not idx:
        return None
    i = idx[-1]
    o = np.array([b["open"] for b in bars], float)
    h = np.array([b["high"] for b in bars], float)
    l = np.array([b["low"] for b in bars], float)
    c = np.array([b["close"] for b in bars], float)
    v = np.array([b["volume"] for b in bars], float)
    t = [b["time_key"][11:16] for b in bars]
    p0 = max(0, i - 30)
    if i == 0:
        return None
    jp = p0 + int(np.argmax(h[p0:i]))
    peak = float(h[jp])
    low = float(np.min(l[jp:i + 1]))                 # 这一段回落到目前为止的最低点
    pb = peak / low - 1
    rngb = h[i] - l[i]
    wick = float((min(o[i], c[i]) - l[i]) / rngb) if rngb > 0 else 0.0
    new_low = bool(l[i] <= np.min(l[max(0, i - 15):i]))
    gap = float(o[0] / prev_close - 1)
    day_move = float(c[i] / o[0] - 1)
    q = {b["time_key"][11:16]: (1 / b["close"] if up else b["close"]) for b in _rth(p, "QQQ", d)}
    qleg = (q[t[i]] / q[t[jp]] - 1) if (t[i] in q and t[jp] in q) else None
    minute = i + 1
    cumv = float(v[:i + 1].sum())
    hot = abs(gap) >= 0.03
    names = (("仍在开盘价下方", "低开≥3%", "QQQ同涨>0.4%") if up else ("仍在开盘价上方", "高开≥3%", "QQQ同跌>0.4%"))
    pts = {names[0]: 2 if day_move >= 0 else 0, names[1]: 1 if gap >= 0.03 else 0,
           "开盘30分钟后": 1 if minute > 30 else 0, names[2]: 1 if (qleg is not None and qleg <= -0.004) else 0}
    sc = min(sum(pts.values()), 4)
    sgn = -1 if up else 1                      # 换回原始价格方向
    r = {"sym": sym, "t": t[i], "price": float(1 / c[i] if up else c[i]),
         "peak": float(1 / peak if up else peak), "peak_t": t[jp], "low": float(1 / low if up else low),
         "pb": pb, "atr": atr, "pb_atr": pb / atr, "depth": depth_bucket(pb / atr), "wick": wick,
         "new_low": new_low, "gap": sgn * gap, "day_move": sgn * day_move, "minute": minute,
         "qqq_leg": (sgn * qleg) if qleg is not None else None,
         "cumvol": cumv, "vol20": vol20, "hot_by_gap": hot, "points": pts, "score": sc,
         "kind": "冲高" if up else "插针"}
    r["odds"] = odds(r)
    return r


def odds(r: dict, heat: str = "热点") -> dict | None:
    """查回测表：同方向、同热度、同深度、同打分的历史结果。「顺向」= 对这笔单有利的方向。"""
    if not LOOKUP.exists():
        return None
    tab = json.loads(LOOKUP.read_text(encoding="utf-8"))
    return tab.get(f"{r.get('kind', '插针')}|{heat}|{r['depth']}|{r['score']}")


def describe(r: dict) -> str:
    """一段话读数。"""
    up = r.get("kind") == "冲高"
    q = f"，QQQ 同期 {r['qqq_leg']:+.2%}" if r.get("qqq_leg") is not None else ""
    move, wick, newx = ("冲高", "上影", "创 15 分钟新高") if up else ("回落", "下影", "创 15 分钟新低")
    depth = r["depth"].replace("回落", move)
    s = (f"{r['sym']} {r['t']}：跳空 {r['gap']:+.1%}，从 {r['peak_t']} 的 {r['peak']:.2f} {move} {r['pb']:.2%}"
         f"（{r['pb_atr']:.2f} 倍日均振幅，{depth}），相对开盘 {r['day_move']:+.2%}，"
         f"开盘后第 {r['minute']} 分钟{q}；这一分钟{wick} {r['wick']:.0%}"
         f"{'、' + newx if r['new_low'] else ''}。打分 {r['score']}/4。")
    o = r.get("odds")
    if o and o.get("all"):
        a = o["all"]
        good, bad = ("回落 2%", "再涨 2%") if up else ("弹 2%", "跌 2%")
        s += (f" 历史同类（热点日·{depth}·{r['score']} 分）{a['n']} 次：30 分钟内{good}占 {a['up2_30']:.0%}，"
              f"{bad}占 {a['down2_30']:.0%}，对你有利方向的 30 分钟平均 {a['ret30']:+.2%}。")
        if a["n"] < 30:
            s += "样本少于 30 次，只作参考。"
    return s
