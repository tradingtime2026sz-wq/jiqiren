"""造模拟成交（老虎订单格式），用于测试和演示。价格由 BS 按给定隐波从标的价格算出。"""
from __future__ import annotations

from datetime import date, datetime

from .config import ET
from .greeks import bs_price, years_between
from .ledger import expiry_dt

_ID = [9000]


def leg(symbol, action, qty, price, expiry=None, strike=None, right=None):
    sec = "OPT" if expiry else "STK"
    return {"symbol": symbol, "sec_type": sec, "expiry": expiry.strftime("%Y%m%d") if expiry else None,
            "strike": strike, "put_call": {"C": "CALL", "P": "PUT"}.get(right), "action": action,
            "qty": qty, "price": round(price, 2)}


def order(ts: datetime, legs: list[dict], is_open=None, fee_per_contract=0.65) -> dict:
    _ID[0] += 1
    ms = int(ts.timestamp() * 1000)
    n = sum(l["qty"] for l in legs if l["sec_type"] == "OPT")
    base = {"id": _ID[0], "order_time": ms, "trade_time": ms, "update_time": ms,
            "status": "Filled", "is_open": is_open, "commission": round(n * fee_per_contract, 2),
            "gst": 0}
    if len(legs) == 1:
        l = legs[0]
        return {**base, "action": l["action"], "filled": l["qty"], "quantity": l["qty"],
                "avg_fill_price": l["price"],
                "contract": {"symbol": l["symbol"], "sec_type": l["sec_type"], "expiry": l["expiry"],
                             "strike": l["strike"], "put_call": l["put_call"],
                             "multiplier": 100 if l["sec_type"] == "OPT" else 1},
                "contract_legs": []}
    return {**base, "action": "BUY", "filled": 1, "quantity": 1, "avg_fill_price": 0,
            "combo_type": "CUSTOM", "contract": {"symbol": legs[0]["symbol"], "sec_type": "MLEG"},
            "contract_legs": [{"symbol": l["symbol"], "sec_type": l["sec_type"], "expiry": l["expiry"],
                               "strike": l["strike"], "put_call": l["put_call"],
                               "action": l["action"], "ratio": 1, "multiplier": 100,
                               "filled_quantity": l["qty"], "avg_filled_price": l["price"]}
                              for l in legs]}


def opt_px(S: float, k: float, exp: date, ts: datetime, iv: float, right: str,
           side: str, r: float = 0.045, half_spread: float = 0.02) -> float:
    """按 BS 定价；买在卖一、卖在买一（各让一点价差）。"""
    p = bs_price(S, k, years_between(ts, expiry_dt(exp)), iv, r, right)
    adj = max(half_spread * p, 0.01)
    return max(p + adj if side == "BUY" else p - adj, 0.01)


def et(y, m, d, hh=10, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)
