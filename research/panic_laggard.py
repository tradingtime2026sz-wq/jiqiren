"""VIX 盘中涨起来时，当天逆势上涨的热门股之后会不会补跌（你 10/22 TSLA 的打法）。

事件日 = 每天最早的 VIX≈+5% 信号（VIXY ≥+2% 且 QQQ 在昨收下方）。
对 65 只票在信号那一分钟按「相对昨收涨跌」分组，看之后到收盘还会跌多少、收盘相对信号时点涨跌。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("/Users/vock/Codes/option-ab-lab/data/1m")
SKIP = {"QQQ", "SPY", "SOXX"}
pd.set_option("display.width", 240)

ev = pd.read_csv("data/research/panic_events.csv")
ev = ev[ev["theta"] == 0.02][["day", "t", "qqq_chg", "qqq_ret5"]]
want = dict(zip(ev["day"], ev["t"]))
rows = []
for p in sorted(SRC.glob("*.csv")):
    if p.stem in SKIP:
        continue
    df = pd.read_csv(p, usecols=["time_key", "open", "high", "low", "close", "volume"])
    df["day"], df["hm"] = df["time_key"].str[:10], df["time_key"].str[11:16]
    prev = df.groupby("day")["close"].last().shift(1).to_dict()
    for d, s in df[df["day"].isin(want)].groupby("day"):
        hm = s["hm"].tolist()
        t = want[d]
        if t not in hm or not np.isfinite(prev.get(d, np.nan)):
            continue
        i = hm.index(t)
        h, l, c, o = s["high"].to_numpy(), s["low"].to_numpy(), s["close"].to_numpy(), s["open"].to_numpy()
        if i + 2 >= len(c):
            continue
        P = c[i]
        rows.append({"sym": p.stem, "day": d, "per": "训练" if d < "2026-01-01" else "检验",
                     "chg_prev": P / prev[d] - 1, "chg_open": P / o[0] - 1,
                     "run_from_low": P / l[:i + 1].min() - 1,
                     "dd": l[i + 1:].min() / P - 1, "up": h[i + 1:].max() / P - 1, "ret_close": c[-1] / P - 1})
x = pd.DataFrame(rows)
x["逆势"] = pd.cut(x["chg_prev"], [-9, -0.02, 0, 0.02, 0.05, 9],
                 labels=["跌>2%", "跌0-2%", "涨0-2%", "涨2-5%", "涨>5%"])


def m(g):
    return pd.Series({"票·天": len(g), "天数": g["day"].nunique(),
                      "到收盘再跌≥2%": round(100 * (g["dd"] <= -0.02).mean()),
                      "到收盘再跌≥4%": round(100 * (g["dd"] <= -0.04).mean()),
                      "反抽≥2%": round(100 * (g["up"] >= 0.02).mean()),
                      "再跌中位%": round(100 * g["dd"].median(), 2),
                      "到收盘平均%": round(100 * g["ret_close"].mean(), 2)})


print("### VIX 涨起来那一刻，按个股相对昨收的位置分组")
print(x.groupby(["逆势", "per"], observed=True)[list(x.columns)].apply(m).to_string())
# 逆势涨、且已经从当日低点拉起来很多（TSLA 10/22 从低点 +3.9%）
x["从低点拉起"] = pd.cut(x["run_from_low"], [-1, 0.02, 0.04, 9], labels=["<2%", "2-4%", ">4%"])
up = x[x["chg_prev"] > 0]
print("\n### 逆势上涨的票 × 已经从当日低点拉起多少")
print(up.groupby(["从低点拉起", "per"], observed=True)[list(up.columns)].apply(m).to_string())
x.to_csv("data/research/panic_laggard.csv", index=False)
