"""Black-Scholes 定价、隐含波动率反推、Delta。只用于盈亏拆解，不做交易建议。"""
from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def bs_price(s: float, k: float, t: float, sigma: float, r: float, right: str) -> float:
    """欧式期权理论价。t 以年计；t<=0 或 sigma<=0 时返回内在价值。"""
    intrinsic = max(s - k, 0.0) if right == "C" else max(k - s, 0.0)
    if t <= 0 or sigma <= 0 or s <= 0:
        return intrinsic
    vt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vt
    d2 = d1 - vt
    if right == "C":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def bs_delta(s: float, k: float, t: float, sigma: float, r: float, right: str) -> float:
    if t <= 0 or sigma <= 0:
        if right == "C":
            return 1.0 if s > k else 0.0
        return -1.0 if s < k else 0.0
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    return _ncdf(d1) if right == "C" else _ncdf(d1) - 1.0


def implied_vol(price: float, s: float, k: float, t: float, r: float, right: str,
                lo: float = 0.005, hi: float = 6.0) -> float | None:
    """二分法反推隐含波动率。价格低于内在价值或无时间价值时返回 None。"""
    if t <= 0 or price <= 0 or s <= 0:
        return None
    if price < bs_price(s, k, t, lo, r, right) - 1e-6:
        return None
    if price > bs_price(s, k, t, hi, r, right):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(s, k, t, mid, r, right) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-5:
            break
    return 0.5 * (lo + hi)


def years_between(t0, t1) -> float:
    """两个时刻之间的年数（日历时间，按 365 天）。"""
    return max((t1 - t0).total_seconds(), 0.0) / (365.0 * 86400.0)
