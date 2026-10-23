"""把回测结果压成查询表：方向（插针 / 冲高）× 热度 × 回落深度档 × 打分 → 历史胜算。

插针 = 向下急跌（买 Call 接）；冲高 = 向上急拉（买 Put 接），由价格取倒数的镜像回测得到，
所以冲高表里的「弹 2%」实际含义是「标的回落 2%」，对买 Put 是有利方向。
"""
from __future__ import annotations

import json
import sys

import pandas as pd

sys.path.insert(0, "research")
from spike_stats import first_per_day, load  # noqa: E402


def depth_bucket(x: float) -> str:
    return "深回落" if x >= 1.0 else "中等回落" if x >= 0.5 else "浅回落"


def score(df: pd.DataFrame) -> pd.Series:
    s = 2 * (df["day_move"] >= 0) + (df["gap"] >= 0.03) + (df["minute"] > 30) + (df["qqq_leg"] <= -0.004)
    return s.clip(upper=4).astype(int)


def cell(g: pd.DataFrame) -> dict:
    return {"n": int(len(g)), "stocks": int(g["sym"].nunique()),
            "up2_30": float((g["mfe30"] >= 0.02).mean()), "down2_30": float((g["mae30"] <= -0.02).mean()),
            "up1_15": float((g["mfe15"] >= 0.01).mean()), "ret30": float(g["ret30"].mean()),
            "mfe30_med": float(g["mfe30"].median()), "mae30_med": float(g["mae30"].median())}


def build(ev: pd.DataFrame, kind: str, out: dict) -> None:
    ev = ev.copy()
    ev["depth"] = ev["pb_atr"].map(depth_bucket)
    for heat_name, mask in (("热点", ev["heat"] != "普通"), ("普通", ev["heat"] == "普通")):
        # 每种深度里各自取「每票每天第一根」：避免当天先出浅针就把后面的深针丢掉
        sub = pd.concat([first_per_day(g) for _, g in ev[mask].groupby("depth")]).copy()
        sub["score"] = score(sub)
        for (d, s), g in sub.groupby(["depth", "score"]):
            key = f"{kind}|{heat_name}|{d}|{s}"
            out[key] = {"all": cell(g)}
            for per, gg in g.groupby("period"):
                out[key]["train" if per.startswith("训练") else "test"] = cell(gg)


if __name__ == "__main__":
    out: dict = {}
    for kind, path in (("插针", "data/research/spike_events.csv"), ("冲高", "data/research/spike_events_up.csv")):
        build(load(path), kind, out)
    json.dump(out, open("data/research/spike_lookup.json", "w"), ensure_ascii=False, indent=1)
    print(len(out), "cells")
