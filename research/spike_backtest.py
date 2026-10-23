"""插针回测：热点票盘中「急跌后创新低、留长下影线」被接住的概率与特征。

数据：option-ab-lab/data/1m（富途 RTH 1 分钟线，2024-10 ~ 2026-10，只读）。
输出：data/research/spike_events.csv（每个事件一行：特征 + 结果）。

用法：.venv/bin/python -I research/spike_backtest.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("/Users/vock/Codes/option-ab-lab/data/1m")
OUT = Path(__file__).resolve().parent.parent / "data" / "research"
SKIP = {"SPY", "QQQ", "SOXX"}            # 指数 ETF 不当热点个股，只作大盘参照
K_NEWLOW = 15          # 创多少分钟新低
PB_LOOK = 30           # 回落起点：过去多少分钟的最高价
WICK_MIN = 0.5         # 下影线占整根 K 线的比例
PB_MIN_ATR = 0.3       # 回落至少是日均振幅的多少倍
HORIZONS = (5, 10, 15, 30, 60)


INVERT = "--up" in sys.argv     # 镜像：研究「冲高」（买 Put 的场景），价格取倒数后冲高就成了插针


def load(sym: str, invert: bool | None = None) -> pd.DataFrame:
    df = pd.read_csv(SRC / f"{sym}.csv", usecols=["time_key", "open", "high", "low", "close", "volume"])
    if INVERT if invert is None else invert:
        hi, lo = df["high"].copy(), df["low"].copy()
        df["open"], df["close"] = 1 / df["open"], 1 / df["close"]
        df["high"], df["low"] = 1 / lo, 1 / hi
    df["day"] = df["time_key"].str[:10]
    df["hm"] = df["time_key"].str[11:16]
    return df


def minute_index(hm: str) -> int:
    h, m = int(hm[:2]), int(hm[3:])
    return (h * 60 + m) - (9 * 60 + 31)


def day_table(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("day", sort=True)
    t = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum(), "n": g.size()})
    t["prev_close"] = t["close"].shift(1)
    t["range_pct"] = (t["high"] - t["low"]) / t["prev_close"]
    t["atr14"] = t["range_pct"].shift(1).rolling(14, min_periods=10).mean()
    t["gap"] = t["open"] / t["prev_close"] - 1
    t["ret5"] = t["prev_close"] / t["close"].shift(6) - 1
    t["vol20"] = t["volume"].shift(1).rolling(20, min_periods=10).mean()
    return t


def cumvol_profiles(df: pd.DataFrame, days: list[str]) -> dict[str, np.ndarray]:
    """每天的「过去 20 天同一分钟的平均累计成交量」，用于盘中相对成交量。"""
    mat = np.full((len(days), 390), np.nan)
    pos = {d: i for i, d in enumerate(days)}
    for d, sub in df.groupby("day"):
        idx = sub["hm"].map(minute_index).to_numpy()
        ok = (idx >= 0) & (idx < 390)
        row = np.zeros(390)
        row[idx[ok]] = sub["volume"].to_numpy()[ok]
        mat[pos[d]] = np.cumsum(row)
    out = {}
    for i, d in enumerate(days):
        if i >= 10:
            out[d] = np.nanmean(mat[max(0, i - 20):i], axis=0)
    return out


def events_for_day(sub: pd.DataFrame, info: pd.Series, ref: np.ndarray | None,
                   qqq: dict | None) -> list[dict]:
    o, h, l, c, v = (sub[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    hm = sub["hm"].tolist()
    n = len(c)
    if n < 60 or not np.isfinite(info["atr14"]) or not np.isfinite(info["prev_close"]):
        return []
    atr = float(info["atr14"])
    rng = h - l
    lower = np.minimum(o, c) - l
    wick = np.divide(lower, rng, out=np.zeros(n), where=rng > 0)
    upper_half = c >= l + 0.5 * rng
    tp = (h + l + c) / 3
    cum_pv, cum_v = np.cumsum(tp * v), np.cumsum(v)
    or_low = float(np.min(l[:5]))
    rets = np.diff(np.log(c), prepend=np.log(o[0]))
    out = []
    leg_peak_t, wicks_in_leg = -1, 0
    for i in range(2, n - 1):
        j0 = max(0, i - K_NEWLOW)
        if l[i] >= np.min(l[j0:i]):
            continue
        p0 = max(0, i - PB_LOOK)
        jp = p0 + int(np.argmax(h[p0:i]))
        peak = float(h[jp])
        pb = peak / l[i] - 1
        if pb < PB_MIN_ATR * atr:
            continue
        if jp != leg_peak_t:
            leg_peak_t, wicks_in_leg = jp, 0
        is_pin = wick[i] >= WICK_MIN and upper_half[i] and rng[i] > 0
        nth = wicks_in_leg + 1 if is_pin else None
        if is_pin:
            wicks_in_leg += 1
        # 急跌接刀（对照组）：本分钟收盘 vs 回落起点，按开盘以来 1 分钟波动标准化
        s1 = float(np.std(rets[max(1, i - 30):i], ddof=1)) if i >= 8 else float("nan")
        m = max(i - jp, 1)
        z = math.log(peak / l[i]) / (s1 * math.sqrt(m)) if s1 and s1 > 0 else float("nan")
        if not is_pin and not (np.isfinite(z) and z >= 2.5):
            continue
        E = float(c[i])
        R = peak - float(l[i])
        ev = {"day": info.name, "t": hm[i], "minute": i + 1, "style": "pin" if is_pin else "knife",
              "nth_wick": nth, "E": E, "peak": peak, "low": float(l[i]),
              "pb_pct": pb, "pb_atr": pb / atr, "z": z, "m": m,
              "wick_ratio": float(wick[i]), "wick_depth": float(lower[i] / E),
              "wick_depth_atr": float(lower[i] / E) / atr,
              "vol_ratio": float(v[i] / np.median(v[max(0, i - 10):i])) if np.median(v[max(0, i - 10):i]) > 0 else np.nan,
              "new_day_low": bool(l[i] < np.min(l[:i])), "below_or": bool(l[i] < or_low),
              "vwap_gap": float(l[i] / (cum_pv[i] / cum_v[i]) - 1) if cum_v[i] > 0 else np.nan,
              "day_move": E / float(o[0]) - 1, "from_prev_close": E / float(info["prev_close"]) - 1,
              "gap": float(info["gap"]), "atr14": atr, "ret5": float(info["ret5"]) if np.isfinite(info["ret5"]) else np.nan,
              "rvol": float(cum_v[i] / ref[i]) if ref is not None and i < len(ref) and ref[i] > 0 else np.nan,
              "day_rvol": float(info["volume"] / info["vol20"]) if info["vol20"] > 0 else np.nan}
        if info["gap"] > 0.005:
            ev["gap_fill"] = (float(o[0]) - float(l[i])) / (float(o[0]) - float(info["prev_close"]))
        if qqq is not None and hm[jp] in qqq and hm[i] in qqq:
            ev["qqq_leg"] = qqq[hm[i]] / qqq[hm[jp]] - 1
        for H in HORIZONS:
            a, b = i + 1, min(n, i + 1 + H)
            ev[f"mfe{H}"] = float(np.max(h[a:b])) / E - 1
            ev[f"mae{H}"] = float(np.min(l[a:b])) / E - 1
            ev[f"ret{H}"] = float(c[b - 1]) / E - 1
            ev[f"full{H}"] = bool(np.max(h[a:b]) >= peak)
        up, dn = E + 0.5 * R, E - 0.5 * R
        first = "none"
        for k in range(i + 1, min(n, i + 31)):
            hu, hd = h[k] >= up, l[k] <= dn
            if hu or hd:
                first = "both" if (hu and hd) else ("up" if hu else "down")
                break
        ev["first_half_r"] = first
        ev["eod_ret"] = float(c[-1]) / E - 1
        ev["eod_full"] = bool(np.max(h[i + 1:]) >= peak)
        out.append(ev)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    syms = sorted(p.stem for p in SRC.glob("*.csv"))
    qdf = load("QQQ")
    qqq_by_day = {d: dict(zip(s["hm"], s["close"])) for d, s in qdf.groupby("day")}
    rows = []
    for k, sym in enumerate(syms):
        if sym in SKIP:
            continue
        df = load(sym)
        dt = day_table(df)
        days = list(dt.index)
        refs = cumvol_profiles(df, days)
        for d, sub in df.groupby("day", sort=True):
            info = dt.loc[d]
            evs = events_for_day(sub.reset_index(drop=True), info, refs.get(d), qqq_by_day.get(d))
            for e in evs:
                e["sym"] = sym
            rows.extend(evs)
        print(f"{k + 1}/{len(syms)} {sym} events so far {len(rows)}", file=sys.stderr, flush=True)
    ev = pd.DataFrame(rows)
    name = "spike_events_up.csv" if INVERT else "spike_events.csv"
    ev.to_csv(OUT / name, index=False)
    print(f"done: {len(ev)} events -> {OUT / name}")


if __name__ == "__main__":
    main()
