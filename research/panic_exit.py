"""恐慌条件叠加 + 持有规则：VIX 盘中涨起来时进 Put，用什么规则离场吃到最多的下跌。

只用每天最早的信号（VIX≈+5%，即 VIXY 比昨收 ≥+2% 且 QQQ 在昨收下方），按天去重。
持有规则都用 QQQ 做空收益代表 Put 方向（期权的凸性另说）。
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "research")
from panic_stats import load, m  # noqa: E402

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 30)


def conds(e: pd.DataFrame) -> pd.DataFrame:
    e = e.copy()
    e["c_加速"] = e["vixy_30m"] > 0.025
    e["c_广度"] = e["breadth_below_vwap"] > 0.85
    e["c_恐慌周"] = e["qqq_ret5"] < -0.03
    e["c_高开转跌"] = e["qqq_gap"] > 0.002
    e["条件数"] = e[["c_加速", "c_广度", "c_恐慌周", "c_高开转跌"]].sum(axis=1).clip(upper=3)
    return e


def exits() -> pd.DataFrame:
    ev = load()
    ev = ev[ev["theta"] == 0.02]
    qq = pd.read_csv("/Users/vock/Codes/option-ab-lab/data/1m/QQQ.csv",
                     usecols=["time_key", "high", "low", "close", "volume"])
    qq["day"], qq["hm"] = qq["time_key"].str[:10], qq["time_key"].str[11:16]
    vx = pd.read_csv("data/research/vixy_1m.csv")
    vx["day"], vx["hm"] = vx["time_key"].str[:10], vx["time_key"].str[11:16]
    qd = {d: s.reset_index(drop=True) for d, s in qq.groupby("day")}
    vd = {d: dict(zip(s["hm"], zip(s["high"], s["close"]))) for d, s in vx.groupby("day")}
    rows = []
    for _, e in ev.iterrows():
        s = qd[e["day"]]
        h, l, c, v, hm = (s["high"].to_numpy(), s["low"].to_numpy(), s["close"].to_numpy(),
                          s["volume"].to_numpy(), s["hm"].tolist())
        i = hm.index(e["t"])
        P = c[i]
        tp = (h + l + c) / 3
        vwap = np.cumsum(tp * v) / np.maximum(np.cumsum(v), 1)
        n = len(c)
        res = {"day": e["day"], "per": e["per"], "max_drop": l[i + 1:].min() / P - 1}
        res["收盘走"] = -(c[-1] / P - 1)
        res["30分钟走"] = -(c[min(n - 1, i + 30)] / P - 1)
        res["60分钟走"] = -(c[min(n - 1, i + 60)] / P - 1)
        vmax, qlow = -1e9, P
        out = {}
        for j in range(i + 1, n):
            vh, vc = vd[e["day"]].get(hm[j], (np.nan, np.nan))
            if np.isfinite(vh):
                vmax = max(vmax, vh)
            qlow = min(qlow, l[j])
            for name, hit in (("VIXY从高点回落1%走", np.isfinite(vc) and vc <= vmax * 0.99),
                              ("VIXY从高点回落2%走", np.isfinite(vc) and vc <= vmax * 0.98),
                              ("QQQ从低点反弹0.4%走", c[j] >= qlow * 1.004),
                              ("QQQ从低点反弹0.7%走", c[j] >= qlow * 1.007),
                              ("QQQ收回均价线走", c[j] > vwap[j] and c[i] < vwap[i])):
                if name not in out and hit:
                    out[name] = -(c[j] / P - 1)
        for name in ("VIXY从高点回落1%走", "VIXY从高点回落2%走", "QQQ从低点反弹0.4%走", "QQQ从低点反弹0.7%走",
                     "QQQ收回均价线走"):
            res[name] = out.get(name, res["收盘走"])
        rows.append(res)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ev = conds(load())
    e1 = ev[ev["theta"] == 0.02]
    print("### 叠加条件数（加速 / 广度 / 恐慌周 / 高开转跌），每天最早信号")
    print(e1.groupby(["条件数", "per"])[list(e1.columns)].apply(m).to_string())
    x = exits()
    rules = [c for c in x.columns if c.endswith("走")]
    rows = []
    for per, g in list(x.groupby("per")) + [("合计", x)]:
        for r in rules:
            pnl = g[r]
            cap = (pnl / (-g["max_drop"])).clip(-3, 1)
            rows.append({"时期": per, "离场规则": r, "天数": len(g), "平均做空收益%": round(100 * pnl.mean(), 2),
                         "赚≥1%占比": round(100 * (pnl >= 0.01).mean()), "亏≥0.5%占比": round(100 * (pnl <= -0.005).mean()),
                         "吃到最大跌幅的比例(中位)": round(100 * cap.median())})
    print("\n### 持有规则对比（QQQ 做空收益代表 Put 方向）")
    print(pd.DataFrame(rows).to_string(index=False))
    x.to_csv("data/research/panic_exits.csv", index=False)
