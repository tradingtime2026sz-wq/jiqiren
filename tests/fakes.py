"""确定性的假行情：日线 + 由日线插值出的分钟线。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from tiger_review.config import ET
from tiger_review.market import Prices


class FakePrices(Prices):
    def __init__(self, paths: dict[str, dict[date, float]]):
        super().__init__(use_futu=False)
        for sym, closes in paths.items():
            rows, prev = [], None
            for d in sorted(closes):
                c = closes[d]
                o = prev if prev is not None else c
                rows.append({"time_key": f"{d} 00:00:00", "open": o, "high": max(o, c) * 1.002,
                             "low": min(o, c) * 0.998, "close": c, "volume": 1})
                prev = c
            self._daily[sym] = rows

    def minute(self, symbol, d):
        row = next((r for r in self._daily.get(symbol, []) if r["time_key"][:10] == d.isoformat()), None)
        if not row:
            return []
        out = []
        t = datetime(d.year, d.month, d.day, 9, 31)
        for i in range(390):
            x = i / 389
            px = row["open"] + (row["close"] - row["open"]) * x
            out.append({"time_key": (t + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                        "open": px, "high": px, "low": px, "close": px, "volume": 1})
        return out


def business_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out
