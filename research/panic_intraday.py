"""盘中「恐慌前半段」：VIX 涨起来以后，QQQ 和热门股还会不会继续大跌。给当天/隔天到期的买 Put 用。

数据（只读）：
- VIXY 1 分钟线（data/research/vixy_1m.csv，富途）：盘中 VIX 的替代
- QQQ 与 65 只热门股 1 分钟线（option-ab-lab/data/1m，到 2026-10-14）
- VIX、VIX3M 日线（vix-ratio/data，芝加哥期权交易所）、QQQ 日线（200 日线）
事件：每天第一次「VIXY 比昨收涨 ≥θ 且 QQQ 低于昨收」的那一分钟（9:45~15:00），θ 分几档。
输出：data/research/panic_events.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("/Users/vock/Codes/option-ab-lab/data/1m")
VR = Path("/Users/vock/Codes/reports/周期判据-QQQ回放-2026-10-17/vix-ratio/data")
OUT = Path("data/research")
THETAS = (0.02, 0.03, 0.04, 0.06)
SKIP = {"QQQ", "SPY", "SOXX"}


def minute_frame(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=["time_key", "open", "high", "low", "close", "volume"])
    df["day"] = df["time_key"].str[:10]
    df["hm"] = df["time_key"].str[11:16]
    return df


def daily_context() -> pd.DataFrame:
    vix = pd.read_csv(VR / "vix_cboe.csv")
    v3 = pd.read_csv(VR / "vix3m_cboe.csv")
    for d in (vix, v3):
        d["day"] = pd.to_datetime(d["DATE"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")
    t = vix[["day", "CLOSE"]].rename(columns={"CLOSE": "vix"}).merge(
        v3[["day", "CLOSE"]].rename(columns={"CLOSE": "vix3m"}), on="day")
    t["ratio"] = t["vix"] / t["vix3m"]
    t["d5"] = t["ratio"] - t["ratio"].shift(5)
    t["max10"] = t["ratio"].rolling(10).max()
    q = pd.read_csv(VR / "qqq_daily.csv")
    dcol = [c for c in q.columns if c.lower() in ("date", "day", "time_key")][0]
    ccol = [c for c in q.columns if c.lower() == "close"][0]
    q["day"] = pd.to_datetime(q[dcol]).dt.strftime("%Y-%m-%d")
    q["ma200"] = q[ccol].rolling(200).mean()
    q["bull"] = q[ccol] > q["ma200"]
    q["qqq_ret5"] = q[ccol] / q[ccol].shift(5) - 1
    t = t.merge(q[["day", "bull", "qqq_ret5"]], on="day", how="left")
    # 用「前一交易日收盘」的状态，盘中可知
    for col in ("vix", "ratio", "d5", "max10", "bull", "qqq_ret5"):
        t[f"{col}_prev"] = t[col].shift(1)

    def state(r):
        if not np.isfinite(r["ratio_prev"]):
            return np.nan
        if r["ratio_prev"] >= 1:
            return "倒挂"
        if r["max10_prev"] >= 0.95 and r["d5_prev"] >= 0.03 and r["ratio_prev"] >= r["max10_prev"] - 0.01:
            return "收窄未倒挂"
        if r["max10_prev"] >= 0.95:
            return "高位震荡"
        return "平静"
    t["vstate"] = t.apply(state, axis=1)
    return t.set_index("day")


def arrays(sub: pd.DataFrame):
    return {k: sub[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume")} | {"hm": sub["hm"].tolist()}


def main() -> None:
    ctx = daily_context()
    vx = pd.read_csv(OUT / "vixy_1m.csv")
    vx["day"], vx["hm"] = vx["time_key"].str[:10], vx["time_key"].str[11:16]
    vdays = {d: s.reset_index(drop=True) for d, s in vx.groupby("day")}
    qq = minute_frame(SRC / "QQQ.csv")
    qdays = {d: s.reset_index(drop=True) for d, s in qq.groupby("day")}
    days = sorted(set(vdays) & set(qdays))
    events = []
    for k, d in enumerate(days):
        if k == 0:
            continue
        pv, pq = vdays[days[k - 1]], qdays[days[k - 1]]
        v, q = vdays[d], qdays[d]
        if len(v) < 300 or len(q) < 300:
            continue
        v_prev, q_prev = float(pv["close"].iloc[-1]), float(pq["close"].iloc[-1])
        vmap = dict(zip(v["hm"], v["close"]))
        vhigh = dict(zip(v["hm"], v["high"]))
        Q = arrays(q)
        tp = (Q["high"] + Q["low"] + Q["close"]) / 3
        vwap = np.cumsum(tp * Q["volume"]) / np.maximum(np.cumsum(Q["volume"]), 1)
        done = set()
        run_vmax = -1e9
        for i, hm in enumerate(Q["hm"]):
            vc = vmap.get(hm)
            if vc is None:
                continue
            run_vmax = max(run_vmax, vhigh.get(hm, vc))
            if not ("09:45" <= hm <= "15:00"):
                continue
            vchg = vc / v_prev - 1
            qchg = Q["close"][i] / q_prev - 1
            for th in THETAS:
                if th in done or vchg < th or qchg >= 0:
                    continue
                done.add(th)
                P = Q["close"][i]
                fut_l, fut_h = Q["low"][i + 1:], Q["high"][i + 1:]
                if len(fut_l) < 5:
                    continue
                # 先到 −0.75% 还是 +0.5%
                first = "none"
                for j in range(i + 1, len(Q["close"])):
                    a, b = Q["low"][j] <= P * (1 - 0.0075), Q["high"][j] >= P * 1.005
                    if a or b:
                        first = "both" if (a and b) else ("down" if a else "up")
                        break
                v30 = vmap.get(Q["hm"][max(0, i - 30)])
                c = ctx.loc[d] if d in ctx.index else None
                low_idx = int(np.argmin(Q["low"][i + 1:])) + i + 1
                events.append({
                    "day": d, "t": hm, "minute": i + 1, "theta": th, "vixy_chg": vchg, "qqq_chg": qchg,
                    "qqq_gap": Q["open"][0] / q_prev - 1,
                    "vixy_30m": (vc / v30 - 1) if v30 else np.nan,
                    "vixy_at_high": bool(vhigh.get(hm, vc) >= run_vmax - 1e-9),
                    "qqq_new_low": bool(Q["low"][i] <= Q["low"][:i + 1].min() + 1e-9),
                    "qqq_vs_vwap": P / vwap[i] - 1,
                    "dd": fut_l.min() / P - 1, "up": fut_h.max() / P - 1,
                    "ret_close": Q["close"][-1] / P - 1, "first": first,
                    "min_to_low": low_idx - i, "low_hm": Q["hm"][low_idx],
                    "vix_prev": c["vix_prev"] if c is not None else np.nan,
                    "ratio_prev": c["ratio_prev"] if c is not None else np.nan,
                    "vstate": c["vstate"] if c is not None else np.nan,
                    "bull": c["bull_prev"] if c is not None else np.nan,
                    "qqq_ret5": c["qqq_ret5_prev"] if c is not None else np.nan,
                })
    ev = pd.DataFrame(events)
    # 热门股广度与跟跌：事件那一分钟有多少票在均价线下方，之后到收盘平均还能跌多少
    want = ev.groupby("day")[["t"]].agg(list)["t"].to_dict()
    br = {(d, t): [] for d, ts in want.items() for t in ts}
    for p in sorted(SRC.glob("*.csv")):
        if p.stem in SKIP:
            continue
        df = minute_frame(p)
        df = df[df["day"].isin(want.keys())]
        for d, s in df.groupby("day"):
            A = arrays(s.reset_index(drop=True))
            tp = (A["high"] + A["low"] + A["close"]) / 3
            vw = np.cumsum(tp * A["volume"]) / np.maximum(np.cumsum(A["volume"]), 1)
            pos = {h: i for i, h in enumerate(A["hm"])}
            for t in want[d]:
                i = pos.get(t)
                if i is None or i + 2 >= len(A["close"]):
                    continue
                P = A["close"][i]
                br[(d, t)].append((P < vw[i], A["low"][i + 1:].min() / P - 1, A["close"][-1] / P - 1))
        print(p.stem, file=sys.stderr, flush=True)
    ev["breadth_below_vwap"] = [np.mean([x[0] for x in br[(d, t)]]) if br[(d, t)] else np.nan for d, t in zip(ev["day"], ev["t"])]
    ev["pool_dd_med"] = [np.median([x[1] for x in br[(d, t)]]) if br[(d, t)] else np.nan for d, t in zip(ev["day"], ev["t"])]
    ev["pool_dd3_share"] = [np.mean([x[1] <= -0.03 for x in br[(d, t)]]) if br[(d, t)] else np.nan for d, t in zip(ev["day"], ev["t"])]
    ev["pool_ret_close_med"] = [np.median([x[2] for x in br[(d, t)]]) if br[(d, t)] else np.nan for d, t in zip(ev["day"], ev["t"])]
    ev.to_csv(OUT / "panic_events.csv", index=False)
    print(len(ev), "events", ev["day"].nunique(), "days")


if __name__ == "__main__":
    main()
