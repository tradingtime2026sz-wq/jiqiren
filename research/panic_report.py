"""恐慌前半段量化报告：单文件 HTML（无 JS，内嵌 SVG）。"""
from __future__ import annotations

import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "research")
sys.path.insert(0, ".")
from panic_stats import baseline, load  # noqa: E402
from spike_report import bars_ret, diverge, pct, table  # noqa: E402
from tiger_review.config import ET  # noqa: E402
from tiger_review.report import CSS, esc  # noqa: E402

OUT = "reports/恐慌前半段量化-2026-10-23.html"


def tail(g):
    return (g["dd"] <= -0.015).mean(), (g["first"] == "up").mean(), len(g)


def main():
    ev = load()
    b = baseline()
    e1 = ev[ev["theta"] == 0.02].copy()
    # 图 1：平时 vs VIX 涨几档
    r1, t1 = [], []
    bt, bu, bn = tail(b)
    r1.append(("平时（QQQ 在昨收下）", bt, bu, bn))
    t1.append(["平时", bn, pct(bt), pct(bu), pct(b["ret_close"].mean(), 2, True)])
    for th, lab in ((0.02, "VIX 约 +5%"), (0.03, "VIX 约 +7%"), (0.04, "VIX 约 +9%"), (0.06, "VIX 约 +13%")):
        g = ev[ev["theta"] == th]
        a, u, n = tail(g)
        r1.append((lab, a, u, n))
        t1.append([lab, n, pct(a), pct(u), pct(g["ret_close"].mean(), 2, True)])
    # 图 2：四个尾巴条件
    conds = [("VIX 30 分钟加速", e1["vixy_30m"] > 0.025), ("热门股 85% 以上在均价线下", e1["breadth_below_vwap"] > 0.85),
             ("QQQ 前 5 天已跌 >3%", e1["qqq_ret5"] < -0.03), ("高开后才转跌", e1["qqq_gap"] > 0.002)]
    r2, t2 = [], []
    for name, msk in conds:
        for flag, g in (("是", e1[msk]), ("否", e1[~msk])):
            a, u, n = tail(g)
            r2.append((f"{name}：{flag}", a, u, n))
            tr, te = g[g["per"] == "训练"], g[g["per"] == "检验"]
            t2.append([name, flag, n, pct(a), pct(u), pct((tr["dd"] <= -0.015).mean()), pct((te["dd"] <= -0.015).mean())])
    # 图 3：离场规则
    x = pd.read_csv("data/research/panic_exits.csv")
    rules = [c for c in x.columns if c.endswith("走")]
    r3 = [(r.replace("走", ""), x[r].mean(), len(x)) for r in rules]
    t3 = [[r.replace("走", ""), len(x), pct(x[r].mean(), 2, True), pct((x[r] >= 0.01).mean()),
           pct((x[r] <= -0.005).mean())] for r in rules]
    # 图 4：逆势强的票
    lg = pd.read_csv("data/research/panic_laggard.csv")
    lg["组"] = pd.cut(lg["run_from_low"], [-1, 0.02, 0.04, 9], labels=["从当日低点拉起 <2%", "拉起 2-4%", "拉起 >4%"])
    up = lg[lg["chg_prev"] > 0]
    r4, t4 = [], []
    for name, g in up.groupby("组", observed=True):
        r4.append((str(name), (g["dd"] <= -0.02).mean(), (g["up"] >= 0.02).mean(), len(g)))
        t4.append([str(name), len(g), pct((g["dd"] <= -0.02).mean()), pct((g["dd"] <= -0.04).mean()),
                   pct((g["up"] >= 0.02).mean()), pct(g["ret_close"].mean(), 2, True)])
    late = ev[ev["theta"] == 0.06]
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>恐慌前半段量化</title><style>{CSS}</style></head>
<body><main>
<h1>恐慌前半段量化：VIX 盘中涨起来，买 Put 拿大赔率什么时候值得</h1>
<div class='sub'>QQQ 与 65 只热门股 1 分钟线、VIXY（盘中 VIX 替代，日涨跌约为 VIX 的 0.47 倍）、VIX/VIX3M 日线；2024-10 至 2026-10，{e1['day'].nunique()} 个 VIX 盘中涨 5% 以上的交易日 · 生成于 {datetime.now(tz=ET):%Y-%m-%d %H:%M} 美东</div>
<div class='card hero'>
<p><b>VIX 盘中大涨不是方向信号，是赔率信号。</b>之后 QQQ 再跌 1.5% 的概率是平时的 2 到 4 倍（约 {pct(tail(ev[ev['theta']==0.03])[0])}，平时 {pct(bt)}），但先反抽 0.5% 的也更多，到收盘平均接近零。</p>
<p><b>四个条件让下跌的尾巴更肥（两期一致）</b>：VIX 30 分钟加速、热门股 85% 以上在均价线下、QQQ 这周已跌 3% 以上、高开后才转跌。反过来，VIX 已涨 13% 左右时先反抽的占 {pct((late['first']=='up').mean())}，VIX/VIX3M 一旦倒挂也是反弹先验。</p>
<p><b>离场决定能不能拿到大赔率。</b>没有一种离场规则能让它变成正收益。拿到收盘，抓到一天再跌 1% 以上的日子占 {pct((x['收盘走']>=0.01).mean())}；VIX 一回落 1% 到 2% 就走，这个比例掉到 {pct((x['VIXY从高点回落1%走']>=0.01).mean())} 到 {pct((x['VIXY从高点回落2%走']>=0.01).mean())}。要拿大赔率，只能小仓位、拿到收盘。</p>
<p class='next'><b>下一步验证</b>：这类单固定小仓位（全亏能接受），至少满足两个尾巴条件才做，拿到收盘，不因小反抽离场；攒 20 笔看实际赔率。</p>
</div>

<h2>VIX 涨得越多，两头都越肥</h2>
<p class='sub'>右边：到收盘前 QQQ 再跌 1.5% 以上的概率；左边：先反抽 0.5% 的概率。每天只算第一次达到该档的时刻。</p>
<div class='card'>{diverge(r1, title_left='先反抽 0.5%', title_right='到收盘再跌 1.5%')}</div>
<div class='card'><p>VIX 涨 5% 到 9% 时，大跌尾巴明显变肥，这是虚值 Put 的赔率来源。</p>
<p>VIX 已经涨了 13% 左右，先反抽的反而最多：涨得太猛，往往已经到了后半段。</p>
<p>到收盘的平均收益接近零：只看 VIX 大涨就做方向，长期是零和。</p></div>
<details><summary>表格</summary>{table(t1, ["分组", "天数", "再跌≥1.5%", "先反抽0.5%", "到收盘平均"])}</details>

<h2>四个让尾巴变肥的条件</h2>
<p class='sub'>都在「VIX 约 +5%、QQQ 在昨收下」的时刻比较，满足 vs 不满足。</p>
<div class='card'>{diverge(r2, title_left='先反抽 0.5%', title_right='到收盘再跌 1.5%')}</div>
<div class='card'><p>VIX 30 分钟内加速（VIXY 涨 2.5% 以上，约等于 VIX 30 分钟涨 5%）：恐慌还在加速。</p>
<p>热门股 85% 以上在均价线下方：卖压是全面的，不是个别板块。</p>
<p>QQQ 这周已经跌了 3% 以上：恐慌在延续，大跌尾巴最肥；叠加两三个条件的样本太少，还看不出叠加效果。</p></div>
<details><summary>表格</summary>{table(t2, ["条件", "是否满足", "天数", "再跌≥1.5%", "先反抽0.5%", "训练期再跌≥1.5%", "检验期再跌≥1.5%"])}</details>

<h2>拿着 Put 什么时候走</h2>
<p class='sub'>用 QQQ 做空收益代表 Put 的方向（期权的凸性另算）。条件都从「VIX 约 +5%」那一分钟进场。</p>
<div class='card'>{bars_ret(r3)}</div>
<div class='card'><p>所有离场规则的平均收益都在零附近，没有哪种能把它变成稳定赚钱。</p>
<p>拿到收盘抓到一天再跌 1% 以上的概率最高；VIX 一回落或 QQQ 一反弹就走，亏得少，但大行情也被砍掉了。</p>
<p>对买虚值 Put 来说，赚钱靠的就是那几天大跌：离场越紧，赔率越小。</p></div>
<details><summary>表格</summary>{table(t3, ["离场规则", "天数", "平均做空收益", "赚≥1%占比", "亏≥0.5%占比"])}</details>

<h2>VIX 涨时，逆势强的热点股会补跌吗</h2>
<p class='sub'>你 10/22 买 TSLA Put 的打法：VIX 在涨，挑当天逆势上涨的热门股。右边：到收盘再跌 2% 的概率；左边：再涨 2% 的概率。</p>
<div class='card'>{diverge(r4, title_left='再涨 2%', title_right='再跌 2%')}</div>
<div class='card'><p>从当日低点拉起越多，后面大跌的概率越高，但大涨的概率同样高。</p>
<p>训练期这类票到收盘平均还在涨，检验期略跌：不是可靠的补跌方向。</p>
<p>它更适合当「波动大的标的」来买虚值期权，而不是当方向判断。</p></div>
<details><summary>表格</summary>{table(t4, ["分组", "票·天", "再跌≥2%", "再跌≥4%", "再涨≥2%", "到收盘平均"])}</details>

<h2>10/22 你 10:49 买 TSLA Put 那一刻</h2>
<div class='card'>
<p>VIXY 比昨收 +6.6%，约 VIX +14%，已在「涨得很猛」那一档；30 分钟内 +2.5%，刚好算加速。</p>
<p>热门股 60 只里 59 只在均价线下方；QQQ 前 5 天 −0.5%，不是恐慌周；低开 1.6%，不是高开转跌。四个条件满足两个。</p>
<p>TSLA 持平昨收、从当日低点拉起 3.4%：历史同类再跌 2% 的约 36%，再涨 2% 的约 45%。</p>
<p>VIXY 一直到 15:02 才见顶，TSLA 在你 11:18 卖出后到收盘又跌了 2.4%：那天拿到收盘，就能吃到这段。</p>
</div>

<h2>盘中怎么看</h2>
<div class='card'>
<p><b>看什么</b>：VIX 分时图（或 VIXY、UVXY 1 分钟线）、QQQ 1 分钟线和均价线、自选热门股是不是大多在均价线下方。</p>
<p><b>做</b>：VIX 涨 5% 到 9%、还在 30 分钟内加速，且满足另外一到两个尾巴条件。</p>
<p><b>不做</b>：VIX 已经涨了 13% 以上，或者 VIX 已经高过 VIX3M（倒挂）；这两种情况历史上反抽更多。</p>
<p><b>拿</b>：仓位按全亏能接受来定；拿到收盘，不因为小反抽离场。历史上用 VIX 回落或 QQQ 反弹做紧止损，大跌日基本都被砍掉了。</p>
</div>

<h2>口径与局限</h2>
<ul class='sub'>
<li>盘中 VIX 用 VIXY 替代（富途不提供 VIX 指数分钟线），VIXY 日涨跌约为 VIX 的 0.47 倍、相关 0.90；文中「VIX 约 +5%」即 VIXY 比昨收 +2%。</li>
<li>结果只看标的价格，没有期权价格历史：VIX 大涨时期权也更贵，赔率要扣掉多付的隐波。</li>
<li>样本：VIX 盘中涨 5% 以上的日子两年只有 {e1['day'].nunique()} 天，分组后很多格只有十几天，条件叠加的效果看不出来；训练期 2024-10 至 2025-12、检验期 2026 年。</li>
<li>日线的「VIX/VIX3M 收窄未倒挂是最弱一段、倒挂是反弹先验」来自另一份研究（VIX 期限结构收窄与见顶回落，2026-10-23），这里只引用。</li>
</ul>
</main></body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    print(OUT)


if __name__ == "__main__":
    main()
