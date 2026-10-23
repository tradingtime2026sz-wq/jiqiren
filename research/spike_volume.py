"""插针接法 × 急跌段量能：急跌那一段（从 30 分钟内高点到针底）的成交量，比前一天 / 前 5 天同一时段是放量还是缩量。

事件沿用 spike_backtest.py 的下影针（data/research/spike_events.csv），只取回落 ≥0.5 倍日均振幅、每只票每天第一根。
结果口径：针那一分钟收盘价进场，之后 30 分钟内先涨 2% 还是先跌 2%（res2）；先弹回半针还是先再跌半针（first_half_r，沿用原回测）；
另给 30 分钟后与收盘的平均收益。

用法：.venv/bin/python -I research/spike_volume.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = Path("/Users/vock/Codes/option-ab-lab/data/1m")
EV = ROOT / "data/research/spike_events.csv"
OUT = ROOT / "data/research/spike_volume.csv"
COLS = ["day", "t", "minute", "m", "sym", "style", "E", "peak", "low", "pb_atr", "gap", "rvol", "qqq_leg",
        "ret30", "eod_ret", "first_half_r", "day_move"]


def mi(hm: str) -> int:
    return int(hm[:2]) * 60 + int(hm[3:]) - (9 * 60 + 31)


def main() -> None:
    ev = pd.read_csv(EV, usecols=COLS)
    ev = ev[(ev["style"] == "pin") & (ev["pb_atr"] >= 0.5)]
    ev = ev.sort_values("minute").groupby(["sym", "day"]).head(1)
    rows = []
    for sym, evs in ev.groupby("sym"):
        df = pd.read_csv(SRC / f"{sym}.csv", usecols=["time_key", "high", "low", "volume"])
        df["day"] = df["time_key"].str[:10]
        df["ci"] = df["time_key"].str[11:16].map(mi)
        df = df[(df["ci"] >= 0) & (df["ci"] < 389)]  # 16:00 那根含收盘竞价，不算
        days = sorted(df["day"].unique())
        pos = {d: k for k, d in enumerate(days)}
        V = np.zeros((len(days), 389)); Hh = np.full((len(days), 389), np.nan); Ll = np.full((len(days), 389), np.nan)
        di = df["day"].map(pos).to_numpy(); ci = df["ci"].to_numpy()
        V[di, ci] = df["volume"].to_numpy(); Hh[di, ci] = df["high"].to_numpy(); Ll[di, ci] = df["low"].to_numpy()
        for e in evs.itertuples(index=False):
            k = pos.get(e.day)
            if k is None or k < 5:
                continue
            c1 = mi(e.t); c0 = max(0, c1 - int(e.m))
            if c1 >= 389:
                continue
            leg = V[k, c0:c1 + 1].sum()
            p1 = V[k - 1, c0:c1 + 1].sum()
            p5 = V[k - 5:k, c0:c1 + 1].sum(axis=1).mean()
            before = V[k, :c0].sum() / c0 if c0 >= 10 else np.nan
            self_r = (leg / (c1 - c0 + 1)) / before if before and before > 0 else np.nan
            # 30 分钟内先涨 2% 还是先跌 2%
            res = "none"
            for j in range(c1 + 1, min(389, c1 + 31)):
                up = Hh[k, j] >= e.E * 1.02; dn = Ll[k, j] <= e.E * 0.98
                if up or dn:
                    res = "both" if (up and dn) else ("up" if up else "down")
                    break
            rows.append(dict(e._asdict(), leg_vol=leg, r1=leg / p1 if p1 > 0 else np.nan,
                             r5=leg / p5 if p5 > 0 else np.nan, r_self=self_r, res2=res))
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    print(f"{len(out)} 事件 -> {OUT}")


if __name__ == "__main__":
    main()
