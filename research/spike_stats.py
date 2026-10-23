"""插针事件统计：热度分层 × 接法 × 特征分桶，训练期 / 检验期分开。"""
from __future__ import annotations

import numpy as np
import pandas as pd

EV = "data/research/spike_events.csv"


def load(path: str = EV) -> pd.DataFrame:
    ev = pd.read_csv(path)
    ev["win"] = ev["first_half_r"] == "up"
    ev["lose"] = ev["first_half_r"] == "down"
    ev["decided"] = ev["first_half_r"].isin(["up", "down"])
    ev["heat"] = np.select(
        [(ev["gap"].abs() >= 0.06) | (ev["rvol"] >= 3), (ev["gap"].abs() >= 0.03) | (ev["rvol"] >= 2)],
        ["很热", "热"], default="普通")
    ev["period"] = np.where(ev["day"] < "2026-01-01", "训练 2024-10~2025-12", "检验 2026")
    ev["bounce1"] = ev["mfe30"] >= 0.01
    return ev


def summary(g: pd.DataFrame) -> pd.Series:
    dec = g[g["decided"]]
    return pd.Series({
        "事件": len(g), "天数": g[["sym", "day"]].drop_duplicates().shape[0],
        "先弹半针": dec["win"].mean() if len(dec) else np.nan,
        "30分钟内弹≥1%": g["bounce1"].mean(),
        "30分钟收复前高": g["full30"].mean(),
        "MFE30中位": g["mfe30"].median(), "MAE30中位": g["mae30"].median(),
        "30分钟后平均": g["ret30"].mean(), "收盘平均": g["eod_ret"].mean(),
    })


def show(df: pd.DataFrame, by: list[str], title: str, min_n: int = 80) -> None:
    t = df.groupby(by, observed=True).apply(summary, include_groups=False)
    t = t[t["事件"] >= min_n]
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 20)
    fmt = t.copy()
    for col in ["先弹半针", "30分钟内弹≥1%", "30分钟收复前高"]:
        fmt[col] = (fmt[col] * 100).round(1)
    for col in ["MFE30中位", "MAE30中位", "30分钟后平均", "收盘平均"]:
        fmt[col] = (fmt[col] * 100).round(2)
    print(f"\n### {title}\n{fmt.to_string()}")


if __name__ == "__main__":
    ev = load()
    pin = ev[ev["style"] == "pin"]
    show(ev, ["style", "heat"], "接法 × 热度（全部）")
    show(ev, ["period", "style", "heat"], "训练 / 检验 分开")
    hot = pin[pin["heat"] != "普通"].copy()
    hot["pb档"] = pd.cut(hot["pb_atr"], [0, 0.5, 0.8, 1.2, 99], labels=["<0.5ATR", "0.5-0.8", "0.8-1.2", ">1.2ATR"])
    hot["影深档"] = pd.cut(hot["wick_depth_atr"], [0, 0.03, 0.06, 0.1, 9], labels=["<3%ATR", "3-6%", "6-10%", ">10%ATR"])
    hot["量档"] = pd.cut(hot["vol_ratio"], [0, 1, 2, 4, 1e9], labels=["<1x", "1-2x", "2-4x", ">4x"])
    hot["时段"] = pd.cut(hot["minute"], [0, 15, 30, 60, 120, 240, 400], labels=["开盘15分", "15-30分", "30-60分", "1-2小时", "2-4小时", "尾盘"])
    hot["VWAP"] = pd.cut(hot["vwap_gap"], [-9, -0.02, -0.01, -0.003, 9], labels=["低于VWAP>2%", "1-2%", "0.3-1%", "贴近或上方"])
    hot["大盘"] = pd.cut(hot["qqq_leg"], [-9, -0.004, -0.0015, 9], labels=["QQQ同跌>0.4%", "QQQ跌0.15-0.4%", "QQQ基本没跌"])
    hot["跳空"] = pd.cut(hot["gap"], [-9, -0.03, -0.005, 0.005, 0.03, 9], labels=["低开>3%", "低开", "平开", "高开", "高开>3%"])
    hot["日内"] = pd.cut(hot["day_move"], [-9, -0.03, -0.01, 0, 0.01, 9], labels=["跌>3%", "跌1-3%", "跌<1%", "涨<1%", "涨>1%"])
    hot["第几根"] = hot["nth_wick"].clip(upper=4).astype(int).astype(str).replace({"4": "4+"})
    for col in ["pb档", "影深档", "量档", "时段", "VWAP", "大盘", "跳空", "日内", "第几根", "new_day_low", "below_or"]:
        show(hot, ["period", col], f"热点日下影针 · {col}", min_n=60)
    hot.to_pickle("data/research/hot_pins.pkl")


def first_per_day(df: pd.DataFrame) -> pd.DataFrame:
    """每只票每天只取第一根，避免同一段下跌被重复计数。"""
    return df.sort_values("minute").groupby(["sym", "day"]).head(1)


def subsets(ev: pd.DataFrame) -> dict[str, pd.DataFrame]:
    hot, nor = ev[ev["heat"] != "普通"], ev[ev["heat"] == "普通"]
    return {
        "热点·深回落·下影接": first_per_day(hot[(hot["style"] == "pin") & (hot["pb_atr"] >= 1.0)]),
        "热点·浅回落·下影接": first_per_day(hot[(hot["style"] == "pin") & (hot["pb_atr"] < 0.5)]),
        "普通·深回落·下影接": first_per_day(nor[(nor["style"] == "pin") & (nor["pb_atr"] >= 1.0)]),
        "热点·深回落·跌中直接接": first_per_day(hot[(hot["style"] == "knife") & (hot["pb_atr"] >= 1.0)]),
    }


def metrics(g: pd.DataFrame) -> pd.Series:
    return pd.Series({
        "事件": len(g), "票数": g["sym"].nunique() if "sym" in g else np.nan,
        "30分钟弹≥2%": round(100 * (g["mfe30"] >= 0.02).mean(), 1),
        "30分钟跌≥2%": round(100 * (g["mae30"] <= -0.02).mean(), 1),
        "15分钟弹≥1%": round(100 * (g["mfe15"] >= 0.01).mean(), 1),
        "MFE30中位%": round(100 * g["mfe30"].median(), 2), "MAE30中位%": round(100 * g["mae30"].median(), 2),
        "30分钟后平均%": round(100 * g["ret30"].mean(), 2), "收盘平均%": round(100 * g["eod_ret"].mean(), 2)})
