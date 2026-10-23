"""把逐腿成交串成「战役」：同一标的从开仓到全部了结的一段完整交易。

- 期权与正股分开成战役；正股持仓只作为判断「备兑 / 保护」的背景。
- 到期未平的腿按到期日收盘价结算：虚值作废、实值按内在价值了结；
  被指派 / 行权交割的正股成交改按到期收盘价入账，差额留在期权战役里，
  这样期权战役的盈亏只反映期权本身。
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .config import ET, Settings
from .ledger import Fill, expiry_dt, leg_label

CASH_SETTLED = {"SPX", "SPXW", "NDX", "NDXP", "RUT", "RUTW", "XSP", "VIX", "VIXW", "DJX"}
CloseFn = Callable[[str, date], "float | None"]


@dataclass
class Campaign:
    cid: str
    underlying: str
    kind: str                                # option / stock
    fills: list[Fill] = field(default_factory=list)
    pos: dict = field(default_factory=lambda: defaultdict(float))
    status: str = "open"                     # open / closed / incomplete
    open_ts: datetime | None = None
    close_ts: datetime | None = None
    last_ts: datetime | None = None
    structure: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    view: str = ""                           # bull / bear / multi / stock

    # ---------- 派生量 ----------
    @property
    def pnl(self) -> float:
        return sum(f.cash for f in self.fills)

    @property
    def fees(self) -> float:
        return sum(f.fee for f in self.fills)

    def opening_fills(self, window_min: int) -> list[Fill]:
        """开仓窗口内、让持仓变大的成交（开仓 / 加仓）。窗口内的平仓、减仓不算开仓。"""
        end = self.open_ts + timedelta(minutes=window_min)
        pos: dict = defaultdict(float)
        out = []
        for f in sorted(self.fills, key=lambda f: f.ts):
            if f.ts > end:
                break
            if f.kind == "trade" and (abs(pos[f.leg]) < 1e-9 or pos[f.leg] * f.qty > 0):
                out.append(f)
            pos[f.leg] += f.qty
        return out

    def is_flat(self) -> bool:
        return all(abs(q) < 1e-9 for q in self.pos.values())


# ---------------------------------------------------------------- 结构识别
def _ratio_pattern(qtys: list[float]) -> list[int]:
    g = 0
    ints = [int(round(abs(q))) for q in qtys]
    for v in ints:
        g = math.gcd(g, v)
    g = g or 1
    return [int(round(q)) // g for q in qtys]


def classify(legs: list[tuple], stock_qty: float = 0.0) -> dict:
    """legs: [(expiry, strike, right, signed_qty)]，返回结构名、方向倾向。"""
    legs = [l for l in legs if abs(l[3]) > 1e-9]
    n = len(legs)
    out = {"code": "custom", "name": f"自定义 {n} 腿", "bias": "混合", "legs": n}
    if n == 0:
        return {"code": "flat", "name": "无持仓", "bias": "无", "legs": 0}
    legs = sorted(legs, key=lambda l: (l[0], l[2], l[1]))
    if n == 1:
        e, k, r, q = legs[0]
        if q > 0:
            return {**out, "code": f"long_{r}", "name": "买 Call" if r == "C" else "买 Put",
                    "bias": "看涨" if r == "C" else "看跌"}
        if r == "P":
            return {**out, "code": "short_P", "name": "卖 Put", "bias": "看涨"}
        covered = stock_qty >= abs(q) * 100 - 1e-6
        return {**out, "code": "covered_C" if covered else "short_C",
                "name": "备兑卖 Call" if covered else "裸卖 Call",
                "bias": "中性偏空" if covered else "看跌"}
    exps = {l[0] for l in legs}
    rights = {l[2] for l in legs}
    if n == 2:
        a, b = sorted(legs, key=lambda l: l[1])
        same_exp, same_right = len(exps) == 1, len(rights) == 1
        if same_right and same_exp and a[3] * b[3] < 0:
            if abs(abs(a[3]) - abs(b[3])) > 1e-9:
                return {**out, "code": "ratio", "name": "比例价差", "bias": "混合"}
            r = a[2]
            if r == "C":
                if a[3] > 0:
                    return {**out, "code": "bull_call", "name": "牛市看涨价差", "bias": "看涨"}
                return {**out, "code": "bear_call", "name": "熊市看涨价差(收权利金)", "bias": "看跌"}
            if b[3] > 0:
                return {**out, "code": "bear_put", "name": "熊市看跌价差", "bias": "看跌"}
            return {**out, "code": "bull_put", "name": "牛市看跌价差(收权利金)", "bias": "看涨"}
        if same_right and not same_exp and a[3] * b[3] < 0:
            far = max(legs, key=lambda l: l[0])
            tag = "日历价差" if a[1] == b[1] else "对角价差"
            bias = "做多波动" if far[3] > 0 else "做空波动"
            if a[1] != b[1]:
                bias = "看涨" if (a[2] == "C") == (far[3] > 0) else "看跌"
            return {**out, "code": "calendar" if a[1] == b[1] else "diagonal", "name": tag,
                    "bias": bias}
        if not same_right and same_exp and a[3] * b[3] > 0:
            straddle = a[1] == b[1]
            if a[3] > 0:
                return {**out, "code": "long_straddle" if straddle else "long_strangle",
                        "name": "买跨式" if straddle else "买宽跨式", "bias": "做多波动"}
            return {**out, "code": "short_straddle" if straddle else "short_strangle",
                    "name": "卖跨式" if straddle else "卖宽跨式", "bias": "做空波动"}
        if not same_right and a[3] * b[3] < 0:
            call = a if a[2] == "C" else b
            return {**out, "code": "risk_reversal",
                    "name": "合成多头/风险逆转" if call[3] > 0 else "合成空头",
                    "bias": "看涨" if call[3] > 0 else "看跌"}
        return {**out, "code": "multi_same", "name": "同向两腿",
                "bias": ("看涨" if (a[2] == "C") == (a[3] > 0) else "看跌")}
    if n == 3 and len(exps) == 1 and len(rights) == 1:
        s = sorted(legs, key=lambda l: l[1])
        pat = _ratio_pattern([l[3] for l in s])
        if pat in ([1, -2, 1], [-1, 2, -1]):
            long_fly = pat[0] > 0
            return {**out, "code": "butterfly", "name": "买蝶式" if long_fly else "卖蝶式",
                    "bias": "做空波动" if long_fly else "做多波动"}
    if n == 4 and len(exps) == 1 and rights == {"C", "P"}:
        puts = sorted([l for l in legs if l[2] == "P"], key=lambda l: l[1])
        calls = sorted([l for l in legs if l[2] == "C"], key=lambda l: l[1])
        if len(puts) == 2 and len(calls) == 2:
            short_inner = puts[1][3] < 0 and puts[0][3] > 0 and calls[0][3] < 0 and calls[1][3] > 0
            long_inner = puts[1][3] > 0 and puts[0][3] < 0 and calls[0][3] > 0 and calls[1][3] < 0
            if short_inner:
                fly = abs(puts[1][1] - calls[0][1]) < 1e-9
                return {**out, "code": "iron_fly" if fly else "iron_condor",
                        "name": "铁蝶" if fly else "铁鹰", "bias": "做空波动"}
            if long_inner:
                return {**out, "code": "reverse_ic", "name": "反向铁鹰", "bias": "做多波动"}
    return out


def defined_risk(legs: list[tuple], credit: float) -> float | None:
    """收权利金的价差类结构的最大亏损（美元）。无法界定返回 None。"""
    by_right = defaultdict(list)
    for e, k, r, q in legs:
        by_right[r].append((k, q))
    widths = []
    for r, items in by_right.items():
        shorts = sum(-q for k, q in items if q < 0)
        longs = sum(q for k, q in items if q > 0)
        if shorts > longs + 1e-9:
            return None
        if shorts > 0:
            ks = sorted(k for k, q in items)
            widths.append((ks[-1] - ks[0]) * shorts * 100)
    if not widths:
        return None
    return max(widths) - credit


def fill_view(fills: list[Fill]) -> str:
    """一笔开仓单的观点：买 Call / 卖 Put = 看涨，买 Put / 卖 Call = 看跌。多腿单记 multi。"""
    views = set()
    for f in fills:
        if f.sec_type != "OPT":
            continue
        views.add("bull" if (f.right == "C") == (f.qty > 0) else "bear")
    if len(views) == 1:
        return views.pop()
    return "multi" if views else "stock"


# ---------------------------------------------------------------- 主流程
class CampaignBuilder:
    window_min = 15

    def __init__(self, settings: Settings, close_fn: CloseFn | None = None,
                 now: datetime | None = None, initial_stock: dict | None = None) -> None:
        self.s = settings
        CampaignBuilder.window_min = settings.leg_window_minutes
        self.close_fn = close_fn or (lambda sym, d: None)
        self.now = now or datetime.now(tz=ET)
        self.window = timedelta(minutes=settings.leg_window_minutes)
        self.open: dict[str, list[Campaign]] = defaultdict(list)
        self.recent: dict[str, list[Campaign]] = defaultdict(list)
        self.all: list[Campaign] = []
        self.stock_pos: dict[str, float] = defaultdict(float, initial_stock or {})
        self.pending_stock: list[Fill] = []
        self._seq = 0

    # ----- 工具 -----
    def _new(self, und: str, kind: str, ts: datetime) -> Campaign:
        self._seq += 1
        c = Campaign(cid=f"{und}-{ts:%y%m%d}-{self._seq:03d}", underlying=und, kind=kind,
                     open_ts=ts, last_ts=ts)
        self.all.append(c)
        self.open[(und, kind)].append(c)
        return c

    def _apply(self, c: Campaign, fills: list[Fill], event: str | None = None) -> None:
        before = dict(c.pos)
        for f in fills:
            c.fills.append(f)
            c.pos[f.leg] += f.qty
        c.last_ts = max(f.ts for f in fills)
        c.events.append(self._event(c, before, fills, event))
        if c.is_flat():
            c.status = "closed" if c.status != "incomplete" else "incomplete"
            c.close_ts = c.last_ts
            self.open[(c.underlying, c.kind)].remove(c)
            self.recent[(c.underlying, c.kind)].append(c)

    @staticmethod
    def _event(c: Campaign, before: dict, fills: list[Fill], forced: str | None) -> dict:
        opened = closed = added = 0
        for f in fills:
            prev = before.get(f.leg, 0.0)
            if abs(prev) < 1e-9:
                opened += 1
            elif prev * f.qty > 0:
                added += 1
            else:
                closed += 1
        in_window = c.open_ts is not None and max(f.ts for f in fills) - c.open_ts <= \
            timedelta(minutes=CampaignBuilder.window_min)
        if forced:
            typ = forced
        elif len(c.events) == 0 or (in_window and not closed):
            typ = "开仓"
        elif opened and closed:
            typ = "移仓"
        elif opened:
            typ = "加腿"
        elif added and not closed:
            typ = "加仓"
        elif closed and c.is_flat():
            typ = "平仓"
        else:
            typ = "减仓"
        return {"ts": max(f.ts for f in fills), "type": typ,
                "legs": [f"{'买' if f.qty > 0 else '卖'}{abs(f.qty):g} {leg_label(f.leg)}"
                         f" @{f.price:g}" for f in fills],
                "cash": sum(f.cash for f in fills)}

    # ----- 到期结算 -----
    def _settle_until(self, t: datetime, fills_ahead: list[Fill]) -> None:
        for key in list(self.open.keys()):
            und, kind = key
            if kind != "option":
                continue
            for c in list(self.open[key]):
                for leg, q in list(c.pos.items()):
                    if abs(q) < 1e-9 or leg[1] != "OPT":
                        continue
                    exp_t = expiry_dt(leg[2])
                    if exp_t >= t:
                        continue
                    self._settle_leg(c, leg, q, fills_ahead)

    def _settle_leg(self, c: Campaign, leg: tuple, q: float, fills_ahead: list[Fill]) -> None:
        und, _, exp, k, r = leg
        s = self.close_fn(und, exp)
        intrinsic = None if s is None else (max(s - k, 0.0) if r == "C" else max(k - s, 0.0))
        shares = abs(q) * 100
        # 找交割的正股成交：到期后 4 天内、数量对得上、价格≈行权价
        deliver = None
        for f in fills_ahead:
            if f.sec_type != "STK" or f.underlying != und or f.kind != "trade":
                continue
            if not (exp <= f.ts.date() <= exp + timedelta(days=4)):
                continue
            if abs(abs(f.qty) - shares) < 1e-6 and abs(f.price - k) < 0.011:
                deliver = f
                break
        note = None
        if deliver is not None:
            kind = "assigned" if q < 0 else "exercised"
            if s is not None:
                deliver.meta["orig_price"] = deliver.price
                deliver.price = s
                deliver.kind = "delivery"
                price = intrinsic
            else:
                price = 0.0
                note = "到期收盘价缺失：交割按行权价入正股，期权按 0 了结"
        elif intrinsic is not None and intrinsic > 0.01:
            kind = "cash_settled" if und in CASH_SETTLED else "settled_itm"
            price = intrinsic
            if und not in CASH_SETTLED:
                note = "实值到期但没找到交割成交，按内在价值了结"
        else:
            kind = "expired"
            price = 0.0
            if s is None:
                note = "到期收盘价缺失，按作废推断"
        f = Fill(order_id=f"settle-{c.cid}-{leg_label(leg)}", ts=expiry_dt(exp), underlying=und,
                 sec_type="OPT", expiry=exp, strike=k, right=r, qty=-q, price=price,
                 multiplier=100, kind=kind)
        label = {"assigned": "被指派", "exercised": "行权交割", "cash_settled": "现金交割",
                 "settled_itm": "实值到期", "expired": "到期作废"}[kind]
        if note:
            c.notes.append(note)
        self._apply(c, [f], event=label)

    # ----- 主循环 -----
    def build(self, fills: list[Fill], option_positions: dict | None = None) -> list[Campaign]:
        orders: dict[str, list[Fill]] = defaultdict(list)
        for f in fills:
            orders[f.order_id].append(f)
        seq = sorted(orders.values(), key=lambda fs: (fs[0].ts, fs[0].order_id))
        flat = [f for fs in seq for f in fs]
        self._fix_broker_expiry(flat)
        for i, ofills in enumerate(seq):
            t = ofills[0].ts
            self._settle_until(t, flat)
            groups = defaultdict(list)
            for f in ofills:
                groups[(f.underlying, "stock" if f.sec_type == "STK" else "option")].append(f)
            for (und, kind), gf in groups.items():
                self._route(und, kind, gf)
        self._settle_until(self.now, flat)
        for c in self.all:
            if c.kind == "option" and c.status in ("open", "closed", "incomplete"):
                self._finalize(c)
        return self.all

    def _fix_broker_expiry(self, flat: list[Fill]) -> None:
        """券商自己记的到期零价平仓：实值被指派时把内在价值补回期权腿。"""
        for f in flat:
            if f.sec_type != "OPT" or f.kind != "trade" or f.price > 0:
                continue
            if f.ts.date() < f.expiry:
                continue
            s = self.close_fn(f.underlying, f.expiry)
            if s is None:
                f.kind = "expired"
                continue
            k, r = f.strike, f.right
            intrinsic = max(s - k, 0.0) if r == "C" else max(k - s, 0.0)
            if intrinsic <= 0.01:
                f.kind = "expired"
                continue
            for g in flat:
                if (g.sec_type == "STK" and g.underlying == f.underlying and g.kind == "trade"
                        and f.expiry <= g.ts.date() <= f.expiry + timedelta(days=4)
                        and abs(abs(g.qty) - abs(f.qty) * 100) < 1e-6
                        and abs(g.price - k) < 0.011):
                    g.meta["orig_price"] = g.price
                    g.price = s
                    g.kind = "delivery"
                    break
            f.price = intrinsic
            f.kind = "assigned" if f.qty > 0 else "exercised"

    def _route(self, und: str, kind: str, gf: list[Fill]) -> None:
        key = (und, kind)
        legs = {f.leg for f in gf}
        held = [c for c in self.open[key] if any(abs(c.pos.get(l, 0)) > 1e-9 for l in legs)]
        target = held[0] if held else None
        for extra in held[1:]:
            self._merge(target, extra)
        t = gf[0].ts
        view = fill_view(gf) if kind == "option" else "stock"
        if target is None and kind == "option":
            near = [c for c in self.open[key] + self.recent[key]
                    if c.last_ts and t - c.last_ts <= self.window
                    and (c.view == view or "multi" in (c.view, view))]
            if near:
                target = max(near, key=lambda c: c.last_ts)
                if target in self.recent[key]:
                    self.recent[key].remove(target)
                    self.open[key].append(target)
                    target.status = "open" if target.status != "incomplete" else "incomplete"
                    target.close_ts = None
        if target is None:
            pre_stock = self.stock_pos.get(und, 0.0)
            target = self._new(und, kind, t)
            target.view = view
            closes_pre = kind == "stock" and abs(pre_stock) > 1e-9 and pre_stock * gf[0].qty < 0
            if any(f.is_open is False for f in gf) or closes_pre:
                target.status = "incomplete"
                target.notes.append("首笔是平仓单：开仓发生在取数区间之前，不计入统计")
        if kind == "stock":
            for f in gf:
                self.stock_pos[und] += f.qty
        if kind == "option" and not target.structure:
            target.structure = {"stock_at_open": self.stock_pos.get(und, 0.0)}
        self._apply(target, gf)

    def _merge(self, keep: Campaign, other: Campaign) -> None:
        keep.fills.extend(other.fills)
        keep.fills.sort(key=lambda f: f.ts)
        for leg, q in other.pos.items():
            keep.pos[leg] += q
        keep.events.extend(other.events)
        keep.events.sort(key=lambda e: e["ts"])
        keep.open_ts = min(keep.open_ts, other.open_ts)
        self.open[(other.underlying, other.kind)].remove(other)
        self.all.remove(other)

    def _finalize(self, c: Campaign) -> None:
        """识别结构、开仓成本、风险基数。"""
        stock_at_open = c.structure.get("stock_at_open", 0.0)
        op = c.opening_fills(self.s.leg_window_minutes)
        net = defaultdict(float)
        for f in op:
            if f.sec_type == "OPT":
                net[(f.expiry, f.strike, f.right)] += f.qty
        legs = [(e, k, r, q) for (e, k, r), q in net.items()]
        st = classify(legs, stock_at_open)
        open_cash = sum(f.cash for f in op)
        debit = -open_cash
        st["side"] = "买方(付权利金)" if debit > 0 else "卖方(收权利金)"
        st["open_debit"] = debit
        st["open_legs"] = legs
        st["stock_at_open"] = stock_at_open
        risk = None
        if debit > 0:
            risk = debit
        else:
            risk = defined_risk(legs, -debit)
        st["risk_basis"] = risk
        cum, peak = 0.0, 0.0
        for f in sorted(c.fills, key=lambda f: f.ts):
            cum += f.cash
            peak = max(peak, -cum)
        st["peak_outlay"] = peak
        c.structure = st
