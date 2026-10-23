"""逐战役还原交易逻辑：进场盘面、方向判断、盈亏来源、持仓过程、离场后行情。

盈亏拆解（每个期权腿的每一段持仓）：
    方向 = BS(S1, T0, σ0) − BS(S0, T0, σ0)      标的价格变化带来的
    时间 = BS(S1, T1, σ0) − BS(S1, T0, σ0)      持有期间时间价值的流逝
    波动 = 平仓价 − BS(S1, T1, σ0)             隐含波动率变化（含买卖价差、模型误差）
三项相加恰好等于 平仓价 − 开仓价（σ0 能从开仓价反推时）。
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from .campaigns import Campaign
from .config import ET, Settings
from .greeks import bs_delta, bs_price, implied_vol, years_between
from .ledger import Fill, expiry_dt, leg_label
from .market import Prices


@dataclass
class Lot:
    leg: tuple
    qty: float            # 带符号：多头正、空头负
    entry_ts: datetime
    entry_px: float
    exit_ts: datetime | None = None
    exit_px: float | None = None
    exit_kind: str | None = None


def match_lots(fills: list[Fill]) -> list[Lot]:
    """按腿先进先出配对开平仓。"""
    queues: dict[tuple, deque] = defaultdict(deque)
    lots: list[Lot] = []
    for f in sorted(fills, key=lambda f: f.ts):
        if f.sec_type != "OPT":
            continue
        q = f.qty
        dq = queues[f.leg]
        while abs(q) > 1e-9 and dq and dq[0].qty * q < 0:
            head = dq[0]
            take = min(abs(q), abs(head.qty))
            sign = 1 if head.qty > 0 else -1
            closed = Lot(f.leg, sign * take, head.entry_ts, head.entry_px, f.ts, f.price, f.kind)
            lots.append(closed)
            head.qty -= sign * take
            q += sign * take
            if abs(head.qty) < 1e-9:
                dq.popleft()
        if abs(q) > 1e-9:
            dq.append(Lot(f.leg, q, f.ts, f.price))
    for dq in queues.values():
        lots.extend(dq)
    return lots


def _hv20(prices: Prices, sym: str, d) -> float | None:
    rows = prices.trading_days_before(sym, d, 21)
    if len(rows) < 15:
        return None
    rets = [math.log(rows[i][1] / rows[i - 1][1]) for i in range(1, len(rows))]
    return statistics.stdev(rets) * math.sqrt(252)


def _bucket_time(ts: datetime) -> str:
    hm = ts.hour * 60 + ts.minute
    if hm < 9 * 60 + 30:
        return "盘前"
    if hm < 10 * 60:
        return "开盘半小时"
    if hm < 12 * 60:
        return "上午"
    if hm < 14 * 60:
        return "午盘"
    if hm <= 16 * 60:
        return "尾盘两小时"
    return "盘后"


class Attributor:
    def __init__(self, prices: Prices, settings: Settings, now: datetime | None = None) -> None:
        self.p = prices
        self.s = settings
        self.r = settings.risk_free_rate
        self.now = now or datetime.now(tz=ET)

    # ------------------------------------------------------------ 单腿定价工具
    def _S(self, und: str, ts: datetime, kind: str | None = None) -> float | None:
        if kind in ("expired", "assigned", "exercised", "cash_settled", "settled_itm"):
            return self.p.close_on(und, ts.date())
        return self.p.price_at(und, ts)

    def _sigma(self, px: float, S: float, leg: tuple, ts: datetime, fallback: float | None):
        _, _, exp, k, r = leg
        T = years_between(ts, expiry_dt(exp))
        iv = implied_vol(px, S, k, T, self.r, r)
        return (iv, True) if iv else (fallback or 0.35, False)

    # ------------------------------------------------------------ 主入口
    def analyze(self, c: Campaign) -> dict:
        und = c.underlying
        st = c.structure
        rec: dict = {
            "cid": c.cid, "underlying": und, "status": c.status,
            "structure": st.get("name"), "structure_code": st.get("code"),
            "bias": st.get("bias"), "side": st.get("side"),
            "open_ts": c.open_ts.isoformat(), "close_ts": c.close_ts.isoformat() if c.close_ts else None,
            "pnl": round(c.pnl, 2), "fees": round(c.fees, 2),
            "open_debit": round(st.get("open_debit", 0.0), 2),
            "risk_basis": st.get("risk_basis"), "peak_outlay": round(st.get("peak_outlay", 0.0), 2),
            "events": [{**e, "ts": e["ts"].isoformat(), "cash": round(e["cash"], 2)} for e in c.events],
            "notes": list(c.notes), "n_adjust": max(len(c.events) - 2, 0),
            "exit_kinds": sorted({f.kind for f in c.fills if f.kind != "trade"}),
        }
        rec["ret"] = self._ret(c)
        rec.update(self._scaling(c))
        rec["note"] = self._note(c)
        rec["spike"] = self._spike(c)
        hv = _hv20(self.p, und, c.open_ts.date())
        rec["hv20"] = hv
        lots = match_lots(c.fills)
        rec.update(self._entry_context(c, lots, hv))
        rec.update(self._strike_reach(c, rec))
        if c.status == "closed":
            rec.update(self._intraday_context(c, rec))
            rec.update(self._exit_position(c, rec))
            rec.update(self._decompose(c, lots, hv))
            rec.update(self._direction(c, rec))
            rec.update(self._path(c, lots, hv))
            rec.update(self._after_exit(c, lots, hv))
        hold = ((c.close_ts or self.now) - c.open_ts)
        rec["hold_days"] = round(hold.total_seconds() / 86400, 5)
        return rec

    def _note(self, c: Campaign) -> dict | None:
        """你补充的选票前提 / 进场逻辑（data/notes.json，键「标的 开仓日期 时:分」）。"""
        from .config import DATA_DIR
        import json as _json
        path = DATA_DIR / "notes.json"
        if not path.exists():
            return None
        notes = _json.loads(path.read_text(encoding="utf-8"))
        return notes.get(f"{c.underlying} {c.open_ts:%Y-%m-%d %H:%M}")

    def _spike(self, c: Campaign) -> dict | None:
        """开仓那一分钟的插针读数（只对常规时段内开仓的单）。"""
        from .spike import reading
        hm = c.open_ts.hour * 60 + c.open_ts.minute
        if not (9 * 60 + 31 <= hm <= 16 * 60):
            return None
        try:
            r = reading(self.p, c.underlying, c.open_ts, "up" if c.view == "bear" else "down")
        except Exception:  # noqa: BLE001
            return None
        return r

    def _scaling(self, c: Campaign) -> dict:
        """加仓是加在浮亏处（摊平）还是浮盈处（顺势加）；离场是一次清、分批还是拿到到期。"""
        avg: dict[tuple, list[float]] = {}
        last_ts: dict[tuple, datetime] = {}
        add_list: list[dict] = []
        add_down = add_up = 0
        for f in sorted(c.fills, key=lambda f: f.ts):
            if f.sec_type != "OPT" or f.kind != "trade":
                continue
            held = avg.get(f.leg)
            prev_ts = last_ts.get(f.leg)
            if held and held[0] * f.qty > 0 and prev_ts and f.ts - prev_ts >= timedelta(minutes=2):
                cost = held[1] / held[0]
                better = f.price < cost if f.qty > 0 else f.price > cost
                add_down += better          # 多头低于成本再买 = 浮亏中加
                add_up += not better
                exits = [g for g in c.fills if g.leg == f.leg and g.ts > f.ts and g.qty * f.qty < 0]
                if exits:
                    q = sum(abs(g.qty) for g in exits)
                    px = sum(abs(g.qty) * g.price for g in exits) / q
                    add_list.append({"ts": f.ts.isoformat(), "price": f.price, "qty": abs(f.qty),
                                     "below_cost": better,
                                     "pnl": round((px - f.price) * f.qty * 100, 2)})
            last_ts[f.leg] = f.ts
            if held is None or held[0] * f.qty >= 0:
                q0, v0 = held or (0.0, 0.0)
                avg[f.leg] = [q0 + f.qty, v0 + f.qty * f.price]
            else:
                q0, v0 = held
                cost = v0 / q0
                avg[f.leg] = [q0 + f.qty, cost * (q0 + f.qty)]
        adds = ("浮亏中加仓(摊平)" if add_down and not add_up else "浮盈中加仓(顺势)"
                if add_up and not add_down else "浮亏浮盈都加过" if add_up else "不加仓")
        closes = [e for e in c.events if e["type"] in ("减仓", "平仓", "移仓")]
        settled = any(f.kind != "trade" for f in c.fills if f.sec_type == "OPT")
        if settled and not closes:
            style = "拿到到期"
        elif settled:
            style = "部分平、余下拿到到期"
        elif any(e["type"] == "移仓" for e in c.events):
            style = "移仓续持"
        elif len(closes) >= 2:
            style = "分批离场"
        else:
            style = "一次清仓"
        return {"adds": adds, "exit_style": style, "add_list": add_list}

    def _ret(self, c: Campaign) -> float | None:
        st = c.structure
        debit = st.get("open_debit", 0.0)
        if debit > 0:
            return round(c.pnl / debit, 4)
        if st.get("risk_basis"):
            return round(c.pnl / st["risk_basis"], 4)
        if debit < 0:
            return round(c.pnl / -debit, 4)
        return None

    # ------------------------------------------------------------ 进场盘面
    def _entry_context(self, c: Campaign, lots: list[Lot], hv: float | None) -> dict:
        und, t0 = c.underlying, c.open_ts
        S0 = self.p.price_at(und, t0)
        out: dict = {"S_open": S0, "entry_bucket": _bucket_time(t0),
                     "weekday": "一二三四五六日"[t0.weekday()]}
        op = [f for f in c.opening_fills(self.s.leg_window_minutes) if f.sec_type == "OPT"]
        if not op:
            return out
        main = max(op, key=lambda f: abs(f.qty * f.price))
        out["main_leg"] = leg_label(main.leg)
        out["dte_open"] = (main.expiry - t0.date()).days
        if S0 is None:
            return out
        # 标的近期走势
        prev = self.p.trading_days_before(und, t0.date(), 21)
        if len(prev) >= 5:
            ref5 = prev[-5][1]
            out["pre5"] = S0 / ref5 - 1
            if hv:
                out["pre5_z"] = out["pre5"] / (hv / math.sqrt(252) * math.sqrt(5))
        if len(prev) >= 20:
            out["ma20_gap"] = S0 / statistics.mean(x[1] for x in prev[-20:]) - 1
        sess = self.p.session_so_far(und, t0)
        if sess:
            out["day_move"] = S0 / sess["open"] - 1
            rng = sess["high"] - sess["low"]
            out["range_pos"] = (S0 - sess["low"]) / rng if rng > 0 else 0.5
        # 主腿隐含波动率与 Delta，组合净 Delta
        iv, ok = self._sigma(main.price, S0, main.leg, t0, hv)
        T = years_between(t0, expiry_dt(main.expiry))
        out["iv_open"] = iv if ok else None
        out["iv_hv"] = (iv / hv) if (ok and hv) else None
        out["delta_main"] = bs_delta(S0, main.strike, T, iv, self.r, main.right)
        net = 0.0
        for f in op:
            ivf, _ = self._sigma(f.price, S0, f.leg, t0, hv)
            Tf = years_between(t0, expiry_dt(f.expiry))
            net += bs_delta(S0, f.strike, Tf, ivf, self.r, f.right) * f.qty * f.multiplier
        out["net_delta_sh"] = net                      # 等效股数
        out["net_delta_usd"] = net * S0                # 等效正股敞口
        return out

    # ------------------------------------------------------------ 行权价可达性
    def _strike_reach(self, c: Campaign, rec: dict) -> dict:
        """单腿买方：从进场到到期（未到期到现在），标的最远朝你的方向走到哪，碰没碰到行权价。"""
        op = [f for f in c.opening_fills(self.s.leg_window_minutes) if f.sec_type == "OPT"]
        if len(op) != 1 and len({f.leg for f in op}) != 1:
            return {}
        f = op[0]
        if f.qty <= 0:
            return {}
        S0 = rec.get("S_open")
        if not S0:
            return {}
        end = min(expiry_dt(f.expiry), self.now)
        bear = f.right == "P"
        best = None
        d = c.open_ts.date()
        if (end - c.open_ts) <= timedelta(days=6):
            while d <= end.date():
                for r in self.p.minute(c.underlying, d):
                    ts = datetime.fromisoformat(r["time_key"]).replace(tzinfo=ET)
                    if c.open_ts <= ts <= end:
                        v = float(r["low"]) if bear else float(r["high"])
                        best = v if best is None else (min(best, v) if bear else max(best, v))
                d += timedelta(days=1)
        else:
            for _, hi, lo, _ in self.p.closes_between(c.underlying, c.open_ts.date(), end.date()):
                v = lo if bear else hi
                best = v if best is None else (min(best, v) if bear else max(best, v))
        if best is None:
            return {}
        reached = best <= f.strike if bear else best >= f.strike
        return {"strike": f.strike, "need_move": f.strike / S0 - 1, "best_move": best / S0 - 1,
                "best_price": best, "strike_reached": reached}

    def _intraday_context(self, c: Campaign, rec: dict) -> dict:
        """进场前 2 小时标的走了多少，持有期间在什么区间。"""
        S0 = rec.get("S_open")
        if not S0 or not c.close_ts:
            return {}
        rows = self.p.minute(c.underlying, c.open_ts.date())
        if not rows:
            return {}
        t2 = (c.open_ts - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        day = c.open_ts.date().isoformat()
        before = [r for r in rows if max(t2, f"{day} 09:31:00") <= r["time_key"]
                  <= c.open_ts.strftime("%Y-%m-%d %H:%M:%S")]
        out = {}
        if before:
            out["pre2h"] = S0 / float(before[0]["open"]) - 1
            out["pre2h_from"] = before[0]["time_key"][11:16]
        if c.close_ts.date() == c.open_ts.date():
            a, b = c.open_ts.strftime("%Y-%m-%d %H:%M:%S"), c.close_ts.strftime("%Y-%m-%d %H:%M:%S")
            hold = [r for r in rows if a <= r["time_key"] <= b]
            if hold:
                out["hold_range"] = [round(min(float(r["low"]) for r in hold), 2),
                                     round(max(float(r["high"]) for r in hold), 2)]
        return out

    # ------------------------------------------------------------ 离场位置
    def _exit_position(self, c: Campaign, rec: dict) -> dict:
        last = max((f for f in c.fills if f.kind == "trade"), key=lambda f: f.ts, default=None)
        if last is None or c.view not in ("bull", "bear"):
            return {}
        t = last.ts
        rows = self.p.minute(c.underlying, t.date())
        lo_s = (t - timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        hi_s = (t + timedelta(minutes=60)).strftime("%Y-%m-%d %H:%M:%S")
        win = [float(r["close"]) for r in rows if lo_s <= r["time_key"] <= hi_s]
        S = self.p.price_at(c.underlying, t)
        if len(win) < 20 or S is None:
            return {}
        hi, lo = max(win), min(win)
        if hi - lo <= 0:
            return {}
        pos = (S - lo) / (hi - lo)
        adverse = pos if c.view == "bear" else 1 - pos     # 看跌时标的越高越不利
        return {"exit_adverse": round(adverse, 3), "exit_window": [round(lo, 2), round(hi, 2)]}

    # ------------------------------------------------------------ 盈亏拆解
    def _decompose(self, c: Campaign, lots: list[Lot], hv: float | None) -> dict:
        comp = defaultdict(float)
        ok_lots = miss = 0
        for lot in lots:
            if lot.exit_ts is None:
                continue
            und, _, exp, k, r = lot.leg
            S0 = self._S(und, lot.entry_ts)
            S1 = self._S(und, lot.exit_ts, lot.exit_kind)
            if S0 is None or S1 is None:
                miss += 1
                continue
            T0 = years_between(lot.entry_ts, expiry_dt(exp))
            T1 = years_between(lot.exit_ts, expiry_dt(exp))
            s0, _ = self._sigma(lot.entry_px, S0, lot.leg, lot.entry_ts, hv)
            p0m = bs_price(S0, k, T0, s0, self.r, r)
            a = bs_price(S1, k, T0, s0, self.r, r)
            b = bs_price(S1, k, T1, s0, self.r, r)
            m = lot.qty * 100
            comp["direction"] += (a - p0m) * m
            comp["time"] += (b - a) * m
            comp["vol"] += (lot.exit_px - b) * m
            comp["model"] += (p0m - lot.entry_px) * m
            ok_lots += 1
        if not ok_lots:
            return {"decomp": None, "decomp_note": "缺标的行情，未拆解"}
        comp["fees"] = -c.fees
        d = {k: round(v, 2) for k, v in comp.items()}
        return {"decomp": d, "decomp_note": f"{miss} 段缺行情未计入" if miss else None}

    # ------------------------------------------------------------ 方向判断
    def _direction(self, c: Campaign, rec: dict) -> dict:
        und = c.underlying
        S0 = rec.get("S_open")
        last = max(c.fills, key=lambda f: f.ts)
        S1 = self._S(und, c.close_ts, last.kind)
        if S0 is None or S1 is None:
            return {}
        out = {"S_close": S1, "move_hold": S1 / S0 - 1}
        # 自然期限：主腿到期日（未到期则到今天）
        exps = [f.expiry for f in c.fills if f.sec_type == "OPT"]
        horizon = min(max(exps), self.now.date()) if exps else c.close_ts.date()
        Sh = self.p.close_on(und, horizon)
        if Sh is None:
            after = self.p.trading_days_before(und, horizon + timedelta(days=1), 1)
            Sh = after[-1][1] if after else None
        out["move_horizon"] = (Sh / S0 - 1) if Sh else None
        bias = rec.get("bias") or ""
        iv = rec.get("iv_open") or rec.get("hv20")
        hold_y = max((c.close_ts - c.open_ts).total_seconds(), 3600) / (365 * 86400)
        exp_move = iv * math.sqrt(hold_y) if iv else None
        out["expected_move_hold"] = exp_move
        nd = rec.get("net_delta_sh") or 0.0

        def years(t_end):
            return max((t_end - c.open_ts).total_seconds(), 3600) / (365 * 86400)

        def flat(move, t_end):
            """涨跌幅不到同期隐含波动幅度的 1/4，算标的基本没动。"""
            return iv is not None and abs(move) < 0.25 * iv * math.sqrt(years(t_end))

        def judge(move, t_end=None):
            if move is None:
                return None
            if t_end is not None and "波动" not in bias and flat(move, t_end):
                return None
            if "做空波动" in bias:
                return abs(move) < exp_move if exp_move else None
            if "做多波动" in bias:
                return abs(move) > exp_move if exp_move else None
            sign = 1 if ("看涨" in bias or (bias in ("混合", "") and nd > 0)) else -1
            if "中性偏空" in bias:
                sign = -1
            return sign * move > 0

        h_end = min(expiry_dt(max(exps)), self.now) if exps else c.close_ts
        out["dir_right_hold"] = judge(out["move_hold"], c.close_ts)
        out["dir_right_horizon"] = judge(out["move_horizon"], h_end)
        out["dir_flat_hold"] = out["dir_right_hold"] is None
        out["dir_flat_horizon"] = out["dir_right_horizon"] is None and out["move_horizon"] is not None
        return out

    # ------------------------------------------------------------ 持仓过程（估算）
    def _lot_value(self, lot: Lot, S: float, t: datetime, sigma: float) -> float:
        _, _, exp, k, r = lot.leg
        return bs_price(S, k, years_between(t, expiry_dt(exp)), sigma, self.r, r)

    def _path(self, c: Campaign, lots: list[Lot], hv: float | None) -> dict:
        und = c.underlying
        t0, t1 = c.open_ts, c.close_ts
        sig, sig1 = {}, {}
        for i, lot in enumerate(lots):
            S0 = self._S(und, lot.entry_ts)
            if S0 is None:
                return {}
            sig[i], _ = self._sigma(lot.entry_px, S0, lot.leg, lot.entry_ts, hv)
            sig1[i] = sig[i]
            if lot.exit_ts is not None and lot.exit_kind == "trade":
                S1 = self._S(und, lot.exit_ts)
                if S1 is not None:
                    v, ok = self._sigma(lot.exit_px, S1, lot.leg, lot.exit_ts, hv)
                    if ok:
                        sig1[i] = v

        def sigma_at(i: int, lot: Lot, ts: datetime) -> float:
            """持有期间隐波在开仓、平仓两个已知值之间按时间线性过渡。"""
            if lot.exit_ts is None or lot.exit_ts <= lot.entry_ts:
                return sig[i]
            w = (ts - lot.entry_ts).total_seconds() / (lot.exit_ts - lot.entry_ts).total_seconds()
            w = min(max(w, 0.0), 1.0)
            return sig[i] + (sig1[i] - sig[i]) * w
        points: list[tuple[datetime, float]] = []
        if (t1 - t0) <= timedelta(days=4):
            d = t0.date()
            while d <= t1.date():
                step = 1 if (t1 - t0) <= timedelta(days=1) else 5
                for r in self.p.minute(und, d)[::step]:
                    ts = datetime.fromisoformat(r["time_key"]).replace(tzinfo=ET)
                    if t0 <= ts <= t1:
                        points += [(ts, float(r["high"])), (ts, float(r["low"]))]
                d += timedelta(days=1)
        if not points:
            for d, hi, lo, cl in self.p.closes_between(und, t0.date(), t1.date()):
                ts = datetime.combine(d, datetime.min.time(), tzinfo=ET).replace(hour=15, minute=59)
                ts = min(max(ts, t0), t1)
                points += [(ts, hi), (ts, lo)]
        if not points:
            return {}
        best, worst = -1e18, 1e18
        for ts, S in points:
            pnl = 0.0
            for i, lot in enumerate(lots):
                if lot.entry_ts > ts:
                    continue
                if lot.exit_ts is not None and lot.exit_ts <= ts:
                    pnl += (lot.exit_px - lot.entry_px) * lot.qty * 100
                else:
                    pnl += (self._lot_value(lot, S, ts, sigma_at(i, lot, ts)) - lot.entry_px) * lot.qty * 100
            best, worst = max(best, pnl), min(worst, pnl)
        gross = c.pnl + c.fees
        best, worst = max(best, gross), min(worst, gross)
        out = {"mfe": round(best, 2), "mae": round(worst, 2)}
        if best > 0 and c.pnl > 0:
            out["capture"] = round(min(c.pnl / best, 1.0), 3)
        return out

    # ------------------------------------------------------------ 离场后
    def _after_exit(self, c: Campaign, lots: list[Lot], hv: float | None) -> dict:
        """离场后：看到「N 个交易日后」和「合约到期」两者中较早的那个时点。

        当天到期的单就看到当天收盘（未收盘则看最新价）。
        """
        und = c.underlying
        trade_exits = [l for l in lots if l.exit_ts and l.exit_kind == "trade"]
        if not trade_exits:
            return {}
        last_exit = max(l.exit_ts for l in trade_exits)
        first_exp = min(expiry_dt(l.leg[2]) for l in trade_exits)
        after = self.p.trading_days_after(und, last_exit.date(), self.s.post_exit_days)
        if len(after) >= self.s.post_exit_days:
            d_n = after[-1][0]
            t_n = datetime.combine(d_n, datetime.min.time(), tzinfo=ET).replace(hour=16)
        else:
            t_n = None
        horizon = min(t_n, first_exp) if t_n else first_exp
        if horizon > self.now:
            horizon = self.now              # 还没到观察终点：看到现在
        S_h = self.p.price_at(und, horizon) if horizon.date() == self.now.date() \
            else self.p.close_on(und, horizon.date())
        S_exit = self._S(und, last_exit)
        if S_h is None or S_exit is None:
            return {}
        missed = 0.0
        for lot in trade_exits:
            S1 = self._S(und, lot.exit_ts)
            if S1 is None:
                continue
            s1, _ = self._sigma(lot.exit_px, S1, lot.leg, lot.exit_ts, hv)
            v_h = self._lot_value(lot, S_h, min(horizon, expiry_dt(lot.leg[2])), s1)
            missed += (v_h - lot.exit_px) * lot.qty * 100
        if horizon == self.now:
            label = "到现在"
        elif horizon.date() == last_exit.date():
            label = "到当天收盘"
        elif horizon == first_exp:
            label = f"到 {horizon:%m/%d} 到期"
        else:
            label = f"{self.s.post_exit_days} 个交易日后"
        return {"post_move": S_h / S_exit - 1, "post_missed": round(missed, 2), "post_label": label}
