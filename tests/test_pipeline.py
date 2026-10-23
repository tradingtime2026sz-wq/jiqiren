from datetime import date, datetime

import pytest

from tiger_review.attribution import Attributor
from tiger_review.campaigns import CampaignBuilder, classify
from tiger_review.config import ET, Settings
from tiger_review.demo import et, leg, opt_px, order
from tiger_review.ledger import order_to_fills, orders_to_fills
from tiger_review.patterns import mine

from fakes import FakePrices, business_days

DAYS = business_days(date(2026, 6, 1), 80)


def path(start=100.0, step=0.0):
    return {d: start * (1 + step) ** i for i, d in enumerate(DAYS)}


def build(orders, prices, now=None):
    s = Settings()
    fills = orders_to_fills(orders)
    b = CampaignBuilder(s, close_fn=prices.close_on, now=now or et(2026, 9, 30, 17))
    camps = b.build(fills)
    return camps, Attributor(prices, s, now=now or et(2026, 9, 30, 17))


def test_mleg_order_splits_into_signed_legs_with_fee():
    exp = date(2026, 7, 17)
    o = order(et(2026, 7, 1), [leg("QQQ", "BUY", 2, 3.0, exp, 500, "C"),
                               leg("QQQ", "SELL", 2, 1.2, exp, 510, "C")])
    fs = order_to_fills(o)
    assert [f.qty for f in fs] == [2, -2]
    assert pytest.approx(sum(f.fee for f in fs)) == 2.6
    assert pytest.approx(sum(f.cash for f in fs)) == -(3.0 - 1.2) * 200 - 2.6


def test_classify_common_structures():
    e = date(2026, 7, 17)
    assert classify([(e, 95, "P", -1), (e, 90, "P", 1)])["code"] == "bull_put"
    assert classify([(e, 100, "C", 1), (e, 100, "P", 1)])["code"] == "long_straddle"
    assert classify([(e, 90, "P", 1), (e, 95, "P", -1), (e, 105, "C", -1), (e, 110, "C", 1)])["code"] == "iron_condor"
    assert classify([(e, 105, "C", -1)], stock_qty=100)["code"] == "covered_C"
    assert classify([(e, 105, "C", -1)], stock_qty=0)["code"] == "short_C"


def test_legging_in_and_roll_stay_in_one_campaign():
    p = FakePrices({"NVDA": path(100, 0.003)})
    e1, e2 = date(2026, 7, 17), date(2026, 8, 21)
    t0 = et(2026, 7, 1, 10, 0)
    orders = [
        order(t0, [leg("NVDA", "BUY", 1, 4.0, e1, 100, "C")]),
        order(et(2026, 7, 1, 10, 5), [leg("NVDA", "SELL", 1, 1.5, e1, 110, "C")]),   # 手动补第二腿
        order(et(2026, 7, 8, 11, 0), [leg("NVDA", "SELL", 1, 6.0, e1, 100, "C"),     # 移仓：平旧开新
                                      leg("NVDA", "BUY", 1, 2.5, e1, 110, "C"),
                                      leg("NVDA", "BUY", 1, 5.0, e2, 105, "C")]),
        order(et(2026, 7, 20, 12, 0), [leg("NVDA", "SELL", 1, 7.0, e2, 105, "C")]),
        order(et(2026, 7, 28, 10, 0), [leg("NVDA", "BUY", 1, 3.0, e2, 115, "C")]),   # 隔很久的新单
    ]
    camps, _ = build(orders, p)
    opt = [c for c in camps if c.kind == "option"]
    assert len(opt) == 2
    first = opt[0]
    assert first.structure["code"] == "bull_call"
    assert [e["type"] for e in first.events] == ["开仓", "开仓", "移仓", "平仓"]
    # 第二笔 8/21 到期、标的已涨过行权价且无交割记录 → 按内在价值了结
    second = opt[1]
    assert second.structure["code"] == "long_C" and second.status == "closed"
    assert [f.kind for f in second.fills][-1] == "settled_itm"
    assert second.notes
    assert first.status == "closed"


def test_expired_worthless_and_assignment_reprice():
    closes = path(100, 0.0)
    exp = date(2026, 7, 17)
    closes[exp] = 90.0                       # 到期日跌到 90
    p = FakePrices({"AMD": closes})
    orders = [
        order(et(2026, 7, 6), [leg("AMD", "SELL", 1, 2.0, exp, 95, "P")]),       # 卖 Put 被指派
        order(et(2026, 7, 6, 10, 30), [leg("AMD", "BUY", 1, 1.0, exp, 110, "C")]),  # 买 Call 作废
        order(et(2026, 7, 20, 9, 31), [leg("AMD", "BUY", 100, 95.0)]),           # 券商交割记录
    ]
    camps, _ = build(orders, p)
    opt = [c for c in camps if c.kind == "option"]
    assert len(opt) == 2
    allf = [f for c in opt for f in c.fills]
    put_settle = [f for f in allf if f.kind == "assigned"]
    call_settle = [f for f in allf if f.kind == "expired"]
    assert put_settle and pytest.approx(put_settle[0].price) == 5.0
    assert call_settle and call_settle[0].price == 0
    stock = [c for c in camps if c.kind == "stock"][0]
    assert pytest.approx(stock.fills[0].price) == 90.0     # 交割按到期收盘价入正股
    put_c = next(c for c in opt if any(f.kind == "assigned" for f in c.fills))
    # 卖 Put 收 200，到期内在价值 5 → 亏 300（未计费）
    assert pytest.approx(sum(f.cash + f.fee for f in put_c.fills if f.right == "P"), abs=0.01) == -300


def test_decomposition_adds_up_to_pnl():
    p = FakePrices({"TSLA": path(200, 0.004)})
    exp = date(2026, 8, 21)
    t0, t1 = et(2026, 7, 1, 10, 0), et(2026, 7, 10, 14, 0)
    S0, S1 = p.price_at("TSLA", t0), p.price_at("TSLA", t1)
    buy = opt_px(S0, 210, exp, t0, 0.60, "C", "BUY")
    sell = opt_px(S1, 210, exp, t1, 0.45, "C", "SELL")       # 方向对，隐波回落
    camps, att = build([order(t0, [leg("TSLA", "BUY", 3, buy, exp, 210, "C")]),
                        order(t1, [leg("TSLA", "SELL", 3, sell, exp, 210, "C")])], p)
    rec = att.analyze(camps[0])
    d = rec["decomp"]
    assert d["direction"] > 0 and d["vol"] < 0 and d["time"] < 0
    assert pytest.approx(sum(d.values()), abs=0.5) == rec["pnl"]
    assert rec["dir_right_hold"] is True


def test_incomplete_when_first_fill_is_close():
    p = FakePrices({"QQQ": path(500)})
    exp = date(2026, 7, 17)
    camps, _ = build([order(et(2026, 7, 1), [leg("QQQ", "SELL", 1, 3.0, exp, 500, "C")],
                            is_open=False)], p)
    assert camps[0].status == "incomplete"


def test_patterns_find_edge_and_leak():
    p = FakePrices({"QQQ": path(500, 0.002), "MU": path(100, -0.004)})
    recs = []
    orders = []
    for i in range(4):   # QQQ 涨：买 Call 赚
        t0 = et(2026, 7, 6 + i, 10)
        t1 = et(2026, 7, 6 + i, 15)
        e = date(2026, 7, 31)
        S0, S1 = p.price_at("QQQ", t0), p.price_at("QQQ", t1)
        orders += [order(t0, [leg("QQQ", "BUY", 1, opt_px(S0, 500, e, t0, .2, "C", "BUY"), e, 500, "C")]),
                   order(t1, [leg("QQQ", "SELL", 1, opt_px(S1 * 1.01, 500, e, t1, .2, "C", "SELL"), e, 500, "C")])]
    for i in range(4):   # MU 跌：买 Call 亏
        t0 = et(2026, 7, 13 + i, 10)
        t1 = et(2026, 7, 13 + i, 15)
        e = date(2026, 7, 24)
        S0, S1 = p.price_at("MU", t0), p.price_at("MU", t1)
        orders += [order(t0, [leg("MU", "BUY", 1, opt_px(S0, 100, e, t0, .6, "C", "BUY"), e, 100, "C")]),
                   order(t1, [leg("MU", "SELL", 1, opt_px(S1 * 0.97, 100, e, t1, .5, "C", "SELL"), e, 100, "C")])]
    camps, att = build(orders, p)
    recs = [att.analyze(c) for c in camps if c.kind == "option"]
    res = mine(recs, min_n=3)
    assert res["edges"] and res["leaks"]
    assert res["overall"]["n"] == 8


def test_call_then_put_are_separate_views():
    p = FakePrices({"QQQ": path(500, 0.001)})
    e = date(2026, 7, 31)
    t0 = et(2026, 7, 6, 10, 0)
    orders = [order(t0, [leg("QQQ", "BUY", 1, 5.0, e, 500, "C")]),
              order(et(2026, 7, 6, 10, 3), [leg("QQQ", "BUY", 1, 4.0, e, 495, "P")]),
              order(et(2026, 7, 6, 11, 0), [leg("QQQ", "SELL", 1, 5.5, e, 500, "C")]),
              order(et(2026, 7, 6, 11, 5), [leg("QQQ", "BUY", 1, 4.0, e, 505, "C")]),   # 同向换行权价=移仓
              order(et(2026, 7, 6, 12, 0), [leg("QQQ", "SELL", 1, 4.2, e, 505, "C")]),
              order(et(2026, 7, 6, 12, 5), [leg("QQQ", "SELL", 1, 3.5, e, 495, "P")])]
    camps, att = build(orders, p)
    opt = [c for c in camps if c.kind == "option"]
    assert sorted(c.view for c in opt) == ["bear", "bull"]
    bull = next(c for c in opt if c.view == "bull")
    assert len({f.leg for f in bull.fills}) == 2 and bull.structure["code"] == "long_C"


def test_scaling_tags():
    p = FakePrices({"MU": path(100, -0.002)})
    e = date(2026, 7, 31)
    orders = [order(et(2026, 7, 6, 10), [leg("MU", "BUY", 1, 5.0, e, 100, "C")]),
              order(et(2026, 7, 7, 10), [leg("MU", "BUY", 1, 3.0, e, 100, "C")]),   # 低于成本再买
              order(et(2026, 7, 8, 10), [leg("MU", "SELL", 1, 3.5, e, 100, "C")]),
              order(et(2026, 7, 9, 10), [leg("MU", "SELL", 1, 3.2, e, 100, "C")])]
    camps, att = build(orders, p)
    rec = att.analyze(camps[0])
    assert rec["adds"] == "浮亏中加仓(摊平)" and rec["exit_style"] == "分批离场"


def test_quick_round_trip_inside_opening_window():
    """开仓后几分钟就分批卖完：结构仍是买 Call，开仓成本是买入金额。"""
    p = FakePrices({"SPCX": path(160, 0.0)})
    e = date(2026, 7, 6)
    orders = [order(et(2026, 7, 6, 9, 44), [leg("SPCX", "BUY", 162, 0.40, e, 165, "C")]),
              order(et(2026, 7, 6, 9, 52), [leg("SPCX", "SELL", 81, 1.44, e, 165, "C")]),
              order(et(2026, 7, 6, 9, 54), [leg("SPCX", "SELL", 81, 1.37, e, 165, "C")])]
    camps, att = build(orders, p)
    c = camps[0]
    assert c.structure["code"] == "long_C" and c.structure["bias"] == "看涨"
    assert pytest.approx(c.structure["open_debit"], abs=0.01) == 162 * 40 + 162 * 0.65
    rec = att.analyze(c)
    assert rec["ret"] > 2
