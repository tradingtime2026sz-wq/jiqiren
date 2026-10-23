"""从订单到报告的完整流程。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from .attribution import Attributor
from .campaigns import CampaignBuilder
from .config import ET, Settings
from .ledger import orders_to_fills
from .market import Prices
from .patterns import mine


def initial_stock(positions: list[dict], fills) -> dict:
    """取数区间开始前的正股持仓 = 当前持仓 − 区间内净买入。"""
    cur = defaultdict(float)
    for p in positions or []:
        c = p.get("contract") or {}
        if (c.get("sec_type") or "").upper() == "STK":
            cur[c.get("symbol")] += float(p.get("quantity") or p.get("position_qty") or 0)
    for f in fills:
        if f.sec_type == "STK":
            cur[f.underlying] -= f.qty
    return {k: v for k, v in cur.items() if abs(v) > 1e-9}


def run(orders: list[dict], prices: Prices, settings: Settings, start: date | None = None,
        end: date | None = None, positions: list[dict] | None = None,
        now: datetime | None = None, account: str = "", demo: bool = False):
    now = now or datetime.now(tz=ET)
    fills = orders_to_fills(orders)
    if not fills:
        raise SystemExit("区间内没有成交记录。")
    first = min(f.ts.date() for f in fills)
    last = max(max(f.ts.date() for f in fills),
               max((f.expiry for f in fills if f.expiry), default=first))
    for und in sorted({f.underlying for f in fills}):
        prices.ensure_daily(und, first - timedelta(days=60),
                            last + timedelta(days=settings.post_exit_days * 2 + 4))
    builder = CampaignBuilder(settings, close_fn=prices.close_on, now=now,
                              initial_stock=initial_stock(positions or [], fills))
    camps = builder.build(fills)
    start = start or first
    end = end or now.date()
    opt = [c for c in camps if c.kind == "option" and start <= c.open_ts.date() <= end]
    incomplete = [c for c in opt if c.status == "incomplete"]
    att = Attributor(prices, settings, now=now)
    recs = [att.analyze(c) for c in opt if c.status != "incomplete"]
    res = mine(recs, settings.min_pattern_n)
    n_open = sum(1 for r in recs if r["status"] == "open")
    settle_notes = sorted({n for c in opt for n in c.notes})
    meta = {
        "range": f"{start:%Y-%m-%d} 至 {end:%Y-%m-%d}", "account": account or settings.account_label,
        "asof": f"{now:%Y-%m-%d %H:%M} 美东", "demo": demo,
        "notes": [
            f"成交订单 {len(orders)} 张，拆成 {len(fills)} 条逐腿成交；期权交易 {len(opt)} 笔，"
            f"其中已了结 {len(recs) - n_open}、仍持有 {n_open}、区间前已开仓未计入 {len(incomplete)}。",
            "一笔交易=同一标的、同一方向从开仓到全部了结；15 分钟内同向换行权价或到期日算移仓，归同一笔。",
            "标的价格：富途 OpenD 分钟线与日线（美东时间）。期权价格路径（最大浮盈浮亏、离场后估值）"
            "按开仓或离场时反推的隐含波动率不变估算，是近似值。",
            "盈亏拆解：方向=标的价格变化，时间损耗=持有期间时间价值流逝，波动率=隐含波动率变化+买卖价差，"
            "三项与费用相加等于实际盈亏，模型误差单列。",
            "方向判断：看涨/看跌按标的涨跌；持有期间=开仓到离场，到期为止=开仓到合约到期日（未到期按最新收盘）。",
            "到期未平的合约：按到期日收盘价判断作废或按内在价值了结；被指派交割的正股按到期收盘价入账。",
        ] + [f"结算推断：{n}" for n in settle_notes]
          + [f"行情缺失：{e}" for e in prices.errors[:10]],
    }
    return res, recs, meta, camps
