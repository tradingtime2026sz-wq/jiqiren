"""盘中恐慌事件统计：再跌概率、按候选指标分组、训练 / 检验两期。"""
from __future__ import annotations

import numpy as np
import pandas as pd

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 30)


def load() -> pd.DataFrame:
    ev = pd.read_csv("data/research/panic_events.csv")
    ev["per"] = np.where(ev["day"] < "2026-01-01", "训练", "检验")
    ev["vix_eq"] = ev["theta"].map({0.02: "VIX≈+5%", 0.03: "VIX≈+7%", 0.04: "VIX≈+9%", 0.06: "VIX≈+13%"})
    return ev


def m(g: pd.DataFrame) -> pd.Series:
    return pd.Series({
        "天数": len(g),
        "再跌≥0.75%": round(100 * (g["dd"] <= -0.0075).mean()),
        "再跌≥1.5%": round(100 * (g["dd"] <= -0.015).mean()),
        "先跌0.75%再说": round(100 * (g["first"] == "down").mean()),
        "先反抽0.5%": round(100 * (g["first"] == "up").mean()),
        "再跌中位%": round(100 * g["dd"].median(), 2), "反抽中位%": round(100 * g["up"].median(), 2),
        "到收盘平均%": round(100 * g["ret_close"].mean(), 2),
        "热门股再跌≥3%占比": round(100 * g["pool_dd3_share"].mean()) if g["pool_dd3_share"].notna().any() else None,
    })


def baseline() -> pd.DataFrame:
    """对照：QQQ 低于昨收、但 VIXY 涨不到 2% 的固定时点（10:30 / 12:00 / 14:00）。"""
    qq = pd.read_csv("/Users/vock/Codes/option-ab-lab/data/1m/QQQ.csv", usecols=["time_key", "high", "low", "close"])
    qq["day"], qq["hm"] = qq["time_key"].str[:10], qq["time_key"].str[11:16]
    vx = pd.read_csv("data/research/vixy_1m.csv")
    vx["day"], vx["hm"] = vx["time_key"].str[:10], vx["time_key"].str[11:16]
    vmap = {(d, h): c for d, h, c in zip(vx["day"], vx["hm"], vx["close"])}
    vprev = vx.groupby("day")["close"].last().shift(1).to_dict()
    qprev = qq.groupby("day")["close"].last().shift(1).to_dict()
    rows = []
    for d, s in qq.groupby("day"):
        if d not in vprev or not np.isfinite(qprev.get(d, np.nan)):
            continue
        h, l, c, hm = s["high"].to_numpy(), s["low"].to_numpy(), s["close"].to_numpy(), s["hm"].tolist()
        for t in ("10:30", "12:00", "14:00"):
            if t not in hm:
                continue
            i = hm.index(t)
            vc = vmap.get((d, t))
            if vc is None or c[i] >= qprev[d] or vc / vprev[d] - 1 >= 0.02:
                continue
            P = c[i]
            first = "none"
            for j in range(i + 1, len(c)):
                a, b = l[j] <= P * 0.9925, h[j] >= P * 1.005
                if a or b:
                    first = "both" if (a and b) else ("down" if a else "up")
                    break
            rows.append({"day": d, "t": t, "dd": l[i + 1:].min() / P - 1, "up": h[i + 1:].max() / P - 1,
                         "ret_close": c[-1] / P - 1, "first": first, "pool_dd3_share": np.nan,
                         "per": "训练" if d < "2026-01-01" else "检验"})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ev = load()
    b = baseline()
    print("### 对照：QQQ 在昨收下方、VIX 没怎么涨（固定时点）")
    print(b.groupby("per")[list(b.columns)].apply(m).to_string())
    print("\n### VIX 盘中涨起来之后（每档阈值每天第一次）")
    print(ev.groupby(["vix_eq", "per"])[list(ev.columns)].apply(m).to_string())
