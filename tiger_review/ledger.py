"""老虎订单 dict → 逐腿成交 Fill。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from .config import ET

EXPIRY_CLOSE = time(16, 0)


@dataclass
class Fill:
    """一条逐腿成交。qty 带符号：买为正、卖为负（期权按张，正股按股）。"""

    order_id: str
    ts: datetime
    underlying: str
    sec_type: str                 # OPT / STK
    expiry: date | None
    strike: float | None
    right: str | None             # C / P
    qty: float
    price: float
    multiplier: float
    fee: float = 0.0
    is_open: bool | None = None
    kind: str = "trade"           # trade / expired / assigned / exercised / cash_settled
    combo: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def leg(self) -> tuple:
        """腿的唯一键。"""
        if self.sec_type == "STK":
            return (self.underlying, "STK", None, None, None)
        return (self.underlying, "OPT", self.expiry, self.strike, self.right)

    @property
    def cash(self) -> float:
        """现金流（收入为正），含费用。"""
        return -self.qty * self.price * self.multiplier - self.fee


def leg_label(leg: tuple) -> str:
    """腿的中文短标签，如 QQQ 10/23 500C。"""
    und, sec, exp, strike, right = leg
    if sec == "STK":
        return f"{und} 正股"
    k = f"{strike:g}"
    return f"{und} {exp.month}/{exp.day} {k}{right}"


def to_et(ms: Any) -> datetime | None:
    if ms in (None, 0, ""):
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=ET)


def parse_expiry(value: Any) -> date | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)) and value > 10**10:
        return datetime.fromtimestamp(value / 1000, tz=ET).date()
    s = str(value).replace("-", "")
    if len(s) == 6:
        s = "20" + s
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _right(value: Any) -> str | None:
    if not value:
        return None
    v = str(value).upper()
    return "C" if v.startswith("C") else "P"


def _sign(action: Any) -> int:
    return 1 if str(action).upper() == "BUY" else -1


def order_to_fills(order: dict) -> list[Fill]:
    """一张订单拆成逐腿 Fill。组合单按 contract_legs 拆，费用按张数分摊。"""
    ts = to_et(order.get("trade_time")) or to_et(order.get("update_time")) \
        or to_et(order.get("order_time"))
    fee_total = float(order.get("commission") or 0) + float(order.get("gst") or 0)
    oid = str(order.get("id"))
    is_open = order.get("is_open")
    combo = order.get("combo_type")
    fills: list[Fill] = []
    legs = order.get("contract_legs") or []
    if legs:
        n_combo = float(order.get("filled") or 0)
        for leg in legs:
            qty = leg.get("filled_quantity")
            if qty in (None, 0):
                qty = n_combo * float(leg.get("ratio") or 1)
            qty = float(qty)
            if qty == 0:
                continue
            sec = (leg.get("sec_type") or "OPT").upper()
            fills.append(Fill(
                order_id=oid, ts=ts, underlying=leg.get("symbol"), sec_type=sec,
                expiry=parse_expiry(leg.get("expiry")) if sec == "OPT" else None,
                strike=float(leg["strike"]) if sec == "OPT" else None,
                right=_right(leg.get("put_call")) if sec == "OPT" else None,
                qty=_sign(leg.get("action")) * qty,
                price=float(leg.get("avg_filled_price") or 0),
                multiplier=float(leg.get("multiplier") or (100 if sec == "OPT" else 1)),
                is_open=is_open, combo=combo))
    else:
        c = order.get("contract") or {}
        sec = (c.get("sec_type") or "STK").upper()
        if sec not in ("OPT", "STK"):
            return []
        fills.append(Fill(
            order_id=oid, ts=ts, underlying=c.get("symbol"), sec_type=sec,
            expiry=parse_expiry(c.get("expiry")) if sec == "OPT" else None,
            strike=float(c["strike"]) if sec == "OPT" else None,
            right=_right(c.get("put_call")) if sec == "OPT" else None,
            qty=_sign(order.get("action")) * float(order.get("filled") or 0),
            price=float(order.get("avg_fill_price") or 0),
            multiplier=float(c.get("multiplier") or (100 if sec == "OPT" else 1)),
            is_open=is_open, combo=combo))
    total_units = sum(abs(f.qty) for f in fills) or 1.0
    for f in fills:
        f.fee = fee_total * abs(f.qty) / total_units
    return fills


def orders_to_fills(orders: list[dict]) -> list[Fill]:
    out: list[Fill] = []
    for o in orders:
        out.extend(order_to_fills(o))
    out.sort(key=lambda f: (f.ts, f.order_id))
    return out


def expiry_dt(expiry: date) -> datetime:
    """期权到期时刻：到期日美东 16:00。"""
    return datetime.combine(expiry, EXPIRY_CLOSE, tzinfo=ET)
