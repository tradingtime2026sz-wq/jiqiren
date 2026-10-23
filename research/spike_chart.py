"""把插针打分翻译成图上能看的条件：几分钟线、哪条均线、什么指标确认。

样本：热点日、回落 ≥0.5 倍日均振幅的针（每票每天每档深度第一根，和 spike_lookup 一致）。
对每根针算：
- 进场那一分钟的图上状态（1 分钟 RSI/KDJ/布林/MA，5 分钟 MA20/布林，均价线，开盘价）
- 六种进场方式的结果：立即接，以及等不同确认信号再接（确认必须在针底后 15 分钟内出现）
输出 data/research/spike_chart.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "research")
from spike_backtest import SRC, load  # noqa: E402
from spike_stats import first_per_day, load as load_events  # noqa: E402

OUT = Path("data/research/spike_chart.csv")
CONFIRM_MAX = 15


def ma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(x).rolling(n).std(ddof=0).to_numpy()
    return s


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au = pd.Series(up).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    ad = pd.Series(dn).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    return 100 - 100 / (1 + np.divide(au, ad, out=np.full(len(c), np.inf), where=ad > 0))


def kdj_j(h, l, c, n=9):
    hh = pd.Series(h).rolling(n, min_periods=1).max().to_numpy()
    ll = pd.Series(l).rolling(n, min_periods=1).min().to_numpy()
    rsv = np.divide(c - ll, hh - ll, out=np.full(len(c), 50.0), where=(hh - ll) > 0) * 100
    k = pd.Series(rsv).ewm(alpha=1 / 3, adjust=False).mean().to_numpy()
    d = pd.Series(k).ewm(alpha=1 / 3, adjust=False).mean().to_numpy()
    return 3 * k - 2 * d


def agg(o, h, l, c, v, n):
    """1 分钟线聚合成 n 分钟线；返回聚合后的数组和每根 1 分钟线所属的 n 分钟线下标。"""
    idx = np.arange(len(c)) // n
    m = idx[-1] + 1
    O = np.array([o[idx == k][0] for k in range(m)])
    H = np.array([h[idx == k].max() for k in range(m)])
    L = np.array([l[idx == k].min() for k in range(m)])
    C = np.array([c[idx == k][-1] for k in range(m)])
    return O, H, L, C, idx


def outcomes(h, l, c, k, E, low):
    n = len(c)
    a, b = k + 1, min(n, k + 31)
    if a >= b:
        return None
    r = {"mfe30": h[a:b].max() / E - 1, "mae30": l[a:b].min() / E - 1, "ret30": c[b - 1] / E - 1}
    # 先弹 2% 还是先跌破针底
    first = "none"
    for j in range(a, b):
        up, dn = h[j] >= E * 1.02, l[j] < low
        if up or dn:
            first = "both" if (up and dn) else ("up2" if up else "break_low")
            break
    r["first"] = first
    return r


def main():
    ev = load_events()
    ev = ev[(ev["heat"] != "普通") & (ev["pb_atr"] >= 0.5)].copy()
    ev["depth"] = np.where(ev["pb_atr"] >= 1, "深回落", "中等回落")
    ev = pd.concat([first_per_day(g) for _, g in ev.groupby("depth")])
    rows = []
    for sym, evs in ev.groupby("sym"):
        df = load(sym)
        days = {d: s.reset_index(drop=True) for d, s in df.groupby("day")}
        for _, e in evs.iterrows():
            s = days.get(e["day"])
            if s is None:
                continue
            o, h, l, c, v = (s[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
            i = int(e["minute"]) - 1
            if i >= len(c) - 2:
                continue
            ma5, ma20 = ma(c, 5), ma(c, 20)
            sd20 = rolling_std(c, 20)
            r14 = rsi(c, 14)
            j1 = kdj_j(h, l, c)
            tp = (h + l + c) / 3
            vwap = np.cumsum(tp * v) / np.cumsum(v)
            O5, H5, L5, C5, idx5 = agg(o, h, l, c, v, 5)
            ma20_5, sd20_5 = ma(C5, 20), rolling_std(C5, 20)
            j5 = kdj_j(H5, L5, C5)
            k5 = idx5[i]
            prev5 = k5 - 1                     # 上一根已完成的 5 分钟线
            low = float(e["low"])
            rec = {"sym": sym, "day": e["day"], "period": e["period"], "depth": e["depth"], "minute": i + 1,
                   "pb_atr": e["pb_atr"], "day_move": e["day_move"], "gap": e["gap"], "qqq_leg": e.get("qqq_leg"),
                   "score": int(min(2 * (e["day_move"] >= 0) + (e["gap"] >= 0.03) + (i + 1 > 30)
                                    + (e["qqq_leg"] <= -0.004 if pd.notna(e["qqq_leg"]) else 0), 4)),
                   "rsi1": r14[i], "j1": j1[i],
                   "below_bb1": bool(np.isfinite(sd20[i]) and low < ma20[i] - 2 * sd20[i]),
                   "below_ma20_1": bool(np.isfinite(ma20[i]) and c[i] < ma20[i]),
                   "dist_ma20_5_atr": ((low / ma20_5[prev5] - 1) / e["atr14"]) if prev5 >= 0 and np.isfinite(ma20_5[prev5]) else np.nan,
                   "below_bb5": bool(prev5 >= 0 and np.isfinite(sd20_5[prev5]) and low < ma20_5[prev5] - 2 * sd20_5[prev5]),
                   "j5": j5[prev5] if prev5 >= 0 else np.nan,
                   "low_vs_vwap": low / vwap[i] - 1, "above_open": bool(c[i] >= o[0])}
            # 进场方式
            entries = {"立即接": i}
            end = min(len(c) - 2, i + CONFIRM_MAX)
            for k in range(i + 1, end + 1):
                if l[k] < low:            # 针底被跌破：确认作废，重新以新低为针底
                    low = float(l[k])
                    continue
                if "1分钟收回MA5" not in entries and c[k] > ma5[k] and c[k] > c[k - 1]:
                    entries["1分钟收回MA5"] = k
                if "1分钟收回MA20" not in entries and np.isfinite(ma20[k]) and c[k] > ma20[k]:
                    entries["1分钟收回MA20"] = k
                if "收回均价线" not in entries and rec["low_vs_vwap"] < 0 and c[k] > vwap[k]:
                    entries["收回均价线"] = k
                kk = idx5[k]
                if "5分钟下影收线" not in entries and (k + 1) % 5 == 0 and kk >= idx5[i]:
                    rng = H5[kk] - L5[kk]
                    if rng > 0 and (min(O5[kk], C5[kk]) - L5[kk]) / rng >= 0.5 and C5[kk] >= L5[kk] + 0.5 * rng:
                        entries["5分钟下影收线"] = k
                if "3分钟阳线反包" not in entries and (k + 1) % 3 == 0 and k >= i + 3:
                    a3, b3 = k - 2, k - 5
                    if b3 >= 0 and c[k] > o[a3] and c[k] > h[b3:a3].max():
                        entries["3分钟阳线反包"] = k
            for name, k in entries.items():
                res = outcomes(h, l, c, k, float(c[k]), float(np.min(l[i:k + 1])))
                if res is None:
                    continue
                rows.append({**rec, "entry": name, "wait_min": k - i, "entry_vs_now": c[k] / c[i] - 1, **res})
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    print(len(out), "rows")


if __name__ == "__main__":
    main()
