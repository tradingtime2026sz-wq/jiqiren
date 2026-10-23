"""演示：用真实标的走势 + 模拟的单腿期权成交，生成一份可检查的复盘。

成交价按 BS 由真实标的价格算出（隐波 = 近 20 日实际波动 × 场景系数），不是你的真实交易。
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

from .config import ET
from .demo import et, leg, opt_px, order
from .market import Prices

SYMS = ["NVDA", "TSLA", "MU", "AMD", "QQQ"]


def _hv(rows, i):
    w = [math.log(rows[j]["close"] / rows[j - 1]["close"]) for j in range(i - 20, i)]
    m = sum(w) / len(w)
    return math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1) * 252)


def _friday_after(d: date, min_days: int) -> date:
    x = d + timedelta(days=min_days)
    while x.weekday() != 4:
        x += timedelta(days=1)
    return x


def _strike(S: float, m: float, step: float) -> float:
    return round(S * m / step) * step


def build_orders(prices: Prices, start: date, end: date, seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    for s in SYMS:
        prices.ensure_daily(s, start - timedelta(days=60), end + timedelta(days=40))
    orders: list[dict] = []
    used: set[tuple] = set()

    def day_rows(sym):
        rows = prices.daily(sym)
        idx = [i for i, r in enumerate(rows) if start.isoformat() <= r["time_key"][:10] <= end.isoformat()
               and i >= 21]
        return rows, idx

    def ts_on(d: date, hh: int, mm: int) -> datetime:
        return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)

    def S_at(sym, ts):
        return prices.price_at(sym, ts)

    def trade(sym, d0, t0, d1, t1, right, k, exp, qty, iv0, iv1, hold_to_expiry=False, side="BUY"):
        S0 = S_at(sym, ts_on(d0, *t0))
        if S0 is None:
            return None
        ts0 = ts_on(d0, *t0)
        act_open, act_close = ("BUY", "SELL") if side == "BUY" else ("SELL", "BUY")
        px0 = opt_px(S0, k, exp, ts0, iv0, right, act_open)
        orders.append(order(ts0, [leg(sym, act_open, qty, px0, exp, k, right)], is_open=True))
        if hold_to_expiry:
            return px0
        ts1 = ts_on(d1, *t1)
        S1 = S_at(sym, ts1)
        px1 = opt_px(S1, k, exp, ts1, iv1, right, act_close)
        orders.append(order(ts1, [leg(sym, act_close, qty, px1, exp, k, right)], is_open=False))
        return px1 - px0

    # 1 追涨买近月虚值 Call：前 5 天大涨后，第二天早盘追，1-2 天走
    for sym in ["NVDA", "TSLA", "MU", "AMD"]:
        rows, idx = day_rows(sym)
        picks = [i for i in idx if i + 3 < len(rows) and
                 rows[i - 1]["close"] / rows[i - 6]["close"] - 1 > 1.1 * _hv(rows, i) / math.sqrt(252) * math.sqrt(5)]
        for i in rnd.sample(picks, min(3, len(picks))):
            d0 = date.fromisoformat(rows[i]["time_key"][:10])
            S = rows[i]["open"]
            hv = _hv(rows, i)
            exp = _friday_after(d0, 3)
            d1 = date.fromisoformat(rows[i + rnd.choice([1, 2])]["time_key"][:10])
            pnl = trade(sym, d0, (9, 50), d1, (15, 20), "C", _strike(S, 1.04, 2.5 if S < 300 else 5), exp,
                        rnd.choice([2, 3]), hv * 1.45, hv * 1.1)
            used.add((sym, d0))
            # 亏了就在 1 小时内加大再开一笔
            if pnl is not None and pnl < 0 and rnd.random() < 0.7 and i + 3 < len(rows):
                d2 = d1
                exp2 = _friday_after(d2, 2)
                d3 = date.fromisoformat(rows[i + 3]["time_key"][:10]) if rows[i + 3]["time_key"][:10] else d2
                S2 = S_at(sym, ts_on(d2, 15, 50)) or S
                trade(sym, d2, (15, 50), d3, (10, 30), "C", _strike(S2, 1.02, 2.5 if S2 < 300 else 5), exp2,
                      5, hv * 1.4, hv * 1.15)

    # 2 逆势抄底买月度偏实值 Call：前 5 天大跌后，午盘进，拿 5 个交易日
    for sym in ["NVDA", "TSLA", "MU", "AMD", "QQQ"]:
        rows, idx = day_rows(sym)
        picks = [i for i in idx if i + 6 < len(rows) and (sym, rows[i]["time_key"][:10]) not in used and
                 rows[i - 1]["close"] / rows[i - 6]["close"] - 1 < -1.0 * _hv(rows, i) / math.sqrt(252) * math.sqrt(5)]
        for i in rnd.sample(picks, min(2, len(picks))):
            d0 = date.fromisoformat(rows[i]["time_key"][:10])
            S = rows[i]["open"]
            hv = _hv(rows, i)
            exp = _friday_after(d0, 28)
            d1 = date.fromisoformat(rows[i + 5]["time_key"][:10])
            trade(sym, d0, (12, 40), d1, (14, 0), "C", _strike(S, 0.97, 2.5 if S < 300 else 5), exp, 1,
                  hv * 1.05, hv * 1.0)

    # 3 0DTE QQQ：顺着开盘后的方向，1-2 小时走
    rows, idx = day_rows("QQQ")
    for i in rnd.sample(idx, min(7, len(idx))):
        d0 = date.fromisoformat(rows[i]["time_key"][:10])
        t0 = (10, rnd.choice([5, 20, 40]))
        S_open, S0 = rows[i]["open"], S_at("QQQ", ts_on(d0, *t0))
        if S0 is None:
            continue
        right = "C" if S0 >= S_open else "P"
        hv = _hv(rows, i)
        t1 = (11 + rnd.choice([0, 1]), rnd.choice([10, 30, 50]))
        trade("QQQ", d0, t0, d0, t1, right, round(S0), d0, rnd.choice([3, 5]), hv * 1.15, hv * 1.05)

    # 4 冲高后买 Put 做回落：前 5 天大涨，1-2 周到期，拿 3 天
    for sym in ["TSLA", "NVDA"]:
        rows, idx = day_rows(sym)
        picks = [i for i in idx if i + 4 < len(rows) and (sym, rows[i]["time_key"][:10]) not in used and
                 rows[i - 1]["close"] / rows[i - 6]["close"] - 1 > 1.3 * _hv(rows, i) / math.sqrt(252) * math.sqrt(5)]
        for i in rnd.sample(picks, min(2, len(picks))):
            d0 = date.fromisoformat(rows[i]["time_key"][:10])
            S, hv = rows[i]["open"], _hv(rows, i)
            exp = _friday_after(d0, 8)
            d1 = date.fromisoformat(rows[i + 3]["time_key"][:10])
            trade(sym, d0, (14, 30), d1, (11, 0), "P", _strike(S, 1.0, 5), exp, 2, hv * 1.3, hv * 1.05)

    # 5 卖 Put 收权利金，拿到到期（可能被指派）
    for sym in ["AMD", "MU"]:
        rows, idx = day_rows(sym)
        for i in rnd.sample(idx[:-15], 2):
            d0 = date.fromisoformat(rows[i]["time_key"][:10])
            S, hv = rows[i]["open"], _hv(rows, i)
            exp = _friday_after(d0, 12)
            k = _strike(S, 0.95, 2.5)
            trade(sym, d0, (11, 0), None, None, "P", k, exp, 1, hv * 1.2, None, hold_to_expiry=True, side="SELL")
            close_e = prices.close_on(sym, exp)
            if close_e is not None and close_e < k:     # 被指派：券商记一笔按行权价买入正股，几天后卖掉
                nxt = exp + timedelta(days=3)
                orders.append(order(ts_on(nxt, 9, 31), [leg(sym, "BUY", 100, k)]))
                orders.append(order(ts_on(nxt + timedelta(days=4), 10, 0),
                                    [leg(sym, "SELL", 100, S_at(sym, ts_on(nxt + timedelta(days=4), 10, 0)) or k)]))

    # 6 摊平：买 Call 后下跌，低位加一倍，再一起卖
    for sym in ["NVDA", "MU"]:
        rows, idx = day_rows(sym)
        picks = [i for i in idx if i + 6 < len(rows) and rows[i + 2]["close"] < rows[i]["open"] * 0.97]
        for i in rnd.sample(picks, min(2, len(picks))):
            d0 = date.fromisoformat(rows[i]["time_key"][:10])
            S, hv = rows[i]["open"], _hv(rows, i)
            exp = _friday_after(d0, 14)
            k = _strike(S, 1.0, 2.5 if S < 300 else 5)
            ts0, ts1 = ts_on(d0, 10, 30), ts_on(date.fromisoformat(rows[i + 2]["time_key"][:10]), 13, 0)
            ts2 = ts_on(date.fromisoformat(rows[i + 6]["time_key"][:10]), 15, 0)
            for ts, q, act in ((ts0, 1, "BUY"), (ts1, 2, "BUY"), (ts2, 3, "SELL")):
                Sx = S_at(sym, ts)
                if Sx is None:
                    break
                px = opt_px(Sx, k, exp, ts, hv * 1.1, "C", act)
                orders.append(order(ts, [leg(sym, act, q, px, exp, k, "C")], is_open=(act == "BUY")))

    # 7 虚值 Call 拿到到期
    for sym in ["TSLA", "AMD"]:
        rows, idx = day_rows(sym)
        i = rnd.choice(idx[:-12])
        d0 = date.fromisoformat(rows[i]["time_key"][:10])
        S, hv = rows[i]["open"], _hv(rows, i)
        trade(sym, d0, (13, 15), None, None, "C", _strike(S, 1.08, 5), _friday_after(d0, 6), 2,
              hv * 1.25, None, hold_to_expiry=True)
    orders.sort(key=lambda o: o["trade_time"])
    return orders
