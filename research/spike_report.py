"""插针量化研究报告：单文件 HTML（无 JS，内嵌 SVG）。"""
from __future__ import annotations

import json
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "research")
from spike_stats import first_per_day, load  # noqa: E402

sys.path.insert(0, ".")
from tiger_review.config import ET  # noqa: E402
from tiger_review.report import CSS, esc  # noqa: E402

OUT = "reports/插针量化-2026-10-21.html"


def pct(x, d=0, sign=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    s = f"{abs(x) * 100:.{d}f}%"
    return (("+" if x > 0 else "−" if x < 0 else "") + s) if sign else s


def diverge(rows, width=660, title_left="30 分钟内先再跌 2%", title_right="30 分钟内弹 2%"):
    """左右对开条：右边=弹≥2% 概率，左边=跌≥2% 概率。rows: (label, up, down, n)。"""
    lab_w, pad, rh = 200, 46, 30
    plot = (width - lab_w - pad * 2) / 2
    zero = lab_w + pad + plot
    h = rh * len(rows) + 30
    out = [f'<svg viewBox="0 0 {width} {h}" class="chart" role="img">',
           f'<text x="{zero - 6}" y="12" class="lab" text-anchor="end">← {title_left}</text>',
           f'<text x="{zero + 6}" y="12" class="lab">{title_right} →</text>',
           f'<line x1="{zero}" y1="18" x2="{zero}" y2="{h}" class="axis"/>']
    m = 0.75
    for i, (lab, up, dn, n) in enumerate(rows):
        y = 22 + i * rh
        wu, wd = up / m * plot, dn / m * plot
        out.append(f'<g><title>{esc(lab)}：弹 2% {pct(up)}，跌 2% {pct(dn)}，{n} 次</title>'
                   f'<text x="{lab_w - 6}" y="{y + 18}" class="lab" text-anchor="end">{esc(lab)}</text>'
                   f'<rect x="{zero - wd:.1f}" y="{y + 6}" width="{max(wd, 1):.1f}" height="16" rx="4" class="barneg"/>'
                   f'<rect x="{zero:.1f}" y="{y + 6}" width="{max(wu, 1):.1f}" height="16" rx="4" class="barpos"/>'
                   f'<text x="{zero - wd - 5:.1f}" y="{y + 18}" class="val" text-anchor="end">{pct(dn)}</text>'
                   f'<text x="{zero + wu + 5:.1f}" y="{y + 18}" class="val">{pct(up)}</text></g>')
    out.append("</svg>")
    return "".join(out)


def bars_ret(rows, width=660):
    """30 分钟平均收益：rows (label, value, n)。"""
    lab_w, pad_l, pad_r, rh = 200, 90, 120, 28
    plot = width - lab_w - pad_l - pad_r
    m = max(abs(v) for _, v, _ in rows) or 0.01
    zero = lab_w + pad_l + plot / 2
    h = rh * len(rows) + 6
    out = [f'<svg viewBox="0 0 {width} {h}" class="chart" role="img">',
           f'<line x1="{zero}" y1="0" x2="{zero}" y2="{h}" class="axis"/>']
    for i, (lab, v, n) in enumerate(rows):
        y = 3 + i * rh
        w = abs(v) / m * (plot / 2)
        x = zero if v >= 0 else zero - w
        out.append(f'<g><title>{esc(lab)}：{pct(v, 2, True)}，{n} 次</title>'
                   f'<text x="{lab_w - 6}" y="{y + 17}" class="lab" text-anchor="end">{esc(lab)}</text>'
                   f'<rect x="{x:.1f}" y="{y + 5}" width="{max(w, 1):.1f}" height="16" rx="4" '
                   f'class="{"barpos" if v >= 0 else "barneg"}"/>'
                   f'<text x="{(zero + w + 5) if v >= 0 else (zero - w - 5):.1f}" y="{y + 17}" class="val" '
                   f'text-anchor="{"start" if v >= 0 else "end"}">{pct(v, 2, True)}（{n}）</text></g>')
    out.append("</svg>")
    return "".join(out)


def table(rows, head):
    return ("<div class='tw'><table><tr>" + "".join(f"<th>{esc(h)}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
            + "</table></div>")


def cellstats(g):
    return ((g["mfe30"] >= 0.02).mean(), (g["mae30"] <= -0.02).mean(), g["ret30"].mean(), len(g),
            (g["mfe15"] >= 0.01).mean())


def main():
    ev = load()
    ev["depth"] = np.where(ev["pb_atr"] >= 1, "深回落", np.where(ev["pb_atr"] >= 0.5, "中等回落", "浅回落"))
    ev["hot"] = np.where(ev["heat"] != "普通", "热点", "普通")
    # 每个「热度 × 深度」里各自取每票每天第一根
    fp = pd.concat([first_per_day(g) for _, g in ev.groupby(["hot", "depth"])]).copy()
    fp["score"] = (2 * (fp["day_move"] >= 0) + (fp["gap"] >= 0.03) + (fp["minute"] > 30)
                   + (fp["qqq_leg"] <= -0.004)).clip(upper=4)
    fp["per"] = np.where(fp["period"].str.startswith("训练"), "训练", "检验")
    n_events, n_days, n_syms = len(ev), ev[["sym", "day"]].drop_duplicates().shape[0], ev["sym"].nunique()

    # 图 1：热度 × 深度
    r1, t1 = [], []
    for hot in ("热点", "普通"):
        for dp in ("浅回落", "中等回落", "深回落"):
            g = fp[(fp["hot"] == hot) & (fp["depth"] == dp)]
            up, dn, ret, n, u15 = cellstats(g)
            r1.append((f"{hot}·{dp}", up, dn, n))
            t1.append([f"{hot}·{dp}", n, pct(up), pct(dn), pct(u15), pct(ret, 2, True)])
    # 图 2：热点深回落 × 打分 × 两期
    deep = fp[(fp["hot"] == "热点") & (fp["depth"] == "深回落")]
    r2, r2b, t2 = [], [], []
    for sc in range(5):
        for per in ("训练", "检验"):
            g = deep[(deep["score"] == sc) & (deep["per"] == per)]
            if len(g) == 0:
                continue
            up, dn, ret, n, u15 = cellstats(g)
            r2.append((f"{sc} 分 · {per}期", ret, n))
            t2.append([f"{sc} 分", per, n, pct(up), pct(dn), pct(u15), pct(ret, 2, True)])
        g = deep[deep["score"] == sc]
        up, dn, ret, n, _ = cellstats(g)
        r2b.append((f"{sc} 分（两期合计）", up, dn, n))
    # 图 3：四个条件各自
    r3, t3 = [], []
    conds = [("仍在开盘价上方", deep["day_move"] >= 0), ("高开 ≥3%", deep["gap"] >= 0.03),
             ("开盘 30 分钟以后", deep["minute"] > 30), ("QQQ 同期跌 >0.4%", deep["qqq_leg"] <= -0.004)]
    for name, m in conds:
        for flag, sub in (("是", deep[m]), ("否", deep[~m])):
            tr = sub[sub["per"] == "训练"]["ret30"].mean()
            te = sub[sub["per"] == "检验"]["ret30"].mean()
            up, dn, ret, n, _ = cellstats(sub)
            r3.append((f"{name}：{flag}", up, dn, n))
            t3.append([name, flag, n, pct(up), pct(dn), pct(tr, 2, True), pct(te, 2, True)])
    # 你的两笔
    yours = [
        ("SPCX 10/21 09:44 买 165C", "高开 8.6%；从 09:32 的 162.77 回落 2.89%，0.66 倍日均振幅（中等回落）；"
         "相对开盘 −1.02%；开盘后第 14 分钟；QQQ 同期 −0.30%；那一分钟下影 82%、刷新当天低点。打分 1。",
         "热点|中等回落|1", "10 分钟后标的 +2.5%，期权 +248%"),
        ("NVDA 10/20 09:34 买 220C", "平开 −0.5%；从 09:31 的 216.46 回落 1.20%，0.56 倍日均振幅（中等回落）；"
         "相对开盘 −0.84%；开盘后第 4 分钟；QQQ 同期 −0.20%；还在下跌途中接（下影 34%），之后又跌了 0.65% 才见底。打分 0。",
         "热点|中等回落|0", "37 分钟后标的 +1.0%，期权 +100%"),
    ]
    lookup = json.load(open("data/research/spike_lookup.json"))
    cards = ""
    for title, body, key, res in yours:
        a = lookup[key]["all"]
        cards += (f"<div class='card'><b>{esc(title)}</b><p>{esc(body)}</p>"
                  f"<p>历史同类 {a['n']} 次：30 分钟内弹 2% 占 {pct(a['up2_30'])}，跌 2% 占 {pct(a['down2_30'])}，"
                  f"15 分钟内弹 1% 占 {pct(a['up1_15'])}，30 分钟平均 {pct(a['ret30'], 2, True)}。</p>"
                  f"<p class='sub'>实际：{esc(res)}。</p></div>")
    d4, d3 = deep[deep["score"] == 4], deep[deep["score"] == 3]
    up4, dn4, ret4, n4, _ = cellstats(d4)
    up3, dn3, ret3, n3, _ = cellstats(d3)
    n4_test = int((d4["per"] == "检验").sum())
    ret4_tr, ret4_te = d4[d4["per"] == "训练"]["ret30"].mean(), d4[d4["per"] == "检验"]["ret30"].mean()
    med_hot = fp[(fp["hot"] == "热点") & (fp["depth"] == "中等回落")]
    upm, dnm, retm, nm, _ = cellstats(med_hot)
    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>插针量化</title><style>{CSS}</style></head>
<body><main>
<h1>插针量化：热点票盘中急跌，什么样的针值得接</h1>
<div class='sub'>{n_syms} 只热门科技与 AI 基建股，2024-10 至 2026-10 的 1 分钟线，{n_days:,} 个交易日·票，{n_events:,} 个候选点 · 生成于 {datetime.now(tz=ET):%Y-%m-%d %H:%M} 美东</div>
<div class='card hero'>
<p><b>插针本身不占优。</b>热点票上中等深度（0.5 到 1 倍日均振幅）的针，接了之后 30 分钟内弹 2% 的占 {pct(upm)}，再跌 2% 的占 {pct(dnm)}，几乎对等（{nm:,} 次）。</p>
<p><b>有优势的只有「热点日 + 回落 ≥1 倍日均振幅 + 打分 4」。</b>30 分钟内弹 2% 占 {pct(up4)}，跌 2% 占 {pct(dn4)}，平均 {pct(ret4, 2, True)}（{n4} 次；训练期 {pct(ret4_tr, 2, True)}，检验期 {pct(ret4_te, 2, True)} 但只有 {n4_test} 次）。3 分只是略好（平均 {pct(ret3, 2, True)}，{n3} 次），0 到 2 分接近零。打分：仍在开盘价上方 2 分，高开 ≥3% 1 分，开盘 30 分钟以后 1 分，QQQ 同期也跌 >0.4% 1 分；4 分就是「仍在开盘价上方」再加另外三条里的两条。</p>
<p><b>你的 SPCX 和 NVDA 都是中等回落、打分 1 和 0。</b>两笔都赚了，但历史同类接近五五开，赚到的是热点日的大波动加上快进快出。</p>
<p class='next'><b>盘中做法</b>：1 分钟 K 线 + MA20。从高点回落超过日均振幅（看「插针速查」）、打分 ≥3、等 1 分钟线收回 MA20 再进，跌破针底就走；攒 20 笔再对照这份统计。</p>
</div>

<h2>热点日才有幅度，深回落才有反弹空间</h2>
<p class='sub'>每根柱子：右边是接针后 30 分钟内先弹 2% 的概率，左边是 30 分钟内再跌 2% 的概率。每只票每天只算第一根针。</p>
<div class='card'>{diverge(r1)}</div>
<div class='card'><p>热点日把两边的概率一起放大：期权买方要的幅度只出现在热点日。</p>
<p>普通日的深回落，弹 2% 和跌 2% 都很少，接了多半是磨时间。</p>
<p>热点日的深回落，两边都接近三成以上，方向优势要靠下面的打分来挑。</p></div>
<details><summary>表格</summary>{table(t1, ["分组", "次数", "30分钟弹≥2%", "30分钟跌≥2%", "15分钟弹≥1%", "30分钟平均"])}</details>

<h2>热点日深回落的针：分数越高，越偏向反弹</h2>
<p class='sub'>训练期 2024-10 至 2025-12 找规律，检验期 2026 年拿来验证。柱子是接针后 30 分钟的平均收益，括号里是次数。</p>
<div class='card'>{bars_ret(r2)}</div>
<div class='card'>{diverge(r2b)}</div>
<div class='card'><p>0 到 2 分的深针平均收益在零附近，检验期多为负：接了是赌。</p>
<p>4 分在两个时期都明显为正，但检验期只有 {n4_test} 次；3 分只比零略好。</p>
<p>同样的打分放在浅、中等回落上没有区分度，深回落是前提。</p></div>
<details><summary>表格</summary>{table(t2, ["打分", "时期", "次数", "30分钟弹≥2%", "30分钟跌≥2%", "15分钟弹≥1%", "30分钟平均"])}</details>

<h2>四个加分条件各自的作用</h2>
<p class='sub'>都在「热点日 + 深回落」里比较：满足这一条 vs 不满足。</p>
<div class='card'>{diverge(r3)}</div>
<div class='card'><p>最强的是「插针时仍在开盘价上方」：强势热点股的深回踩，最容易被接住。</p>
<p>「开盘 30 分钟以后」和「QQQ 一起跌」都让反弹更可能：前者避开开盘的乱砸，后者说明是大盘拖累而不是个股出事。</p>
<p>成交量放大、第几次探底、下影线长短，两个时期结论相反，没有放进打分。</p></div>
<details><summary>表格</summary>{table(t3, ["条件", "是否满足", "次数", "30分钟弹≥2%", "30分钟跌≥2%", "训练期平均", "检验期平均"])}</details>

<h2>你的两笔放进来看</h2>
{cards}

<h2>盘中怎么用：只看 1 分钟 K 线 + MA20</h2>
<div class='card'>
<p><b>1. 够不够深</b>：从当天高点回落超过这只票的日均振幅（「插针速查」表里的数字，每天收盘后自动更新）。</p>
<p><b>2. 打分 ≥3</b>：插针时仍在开盘价上方 2 分；高开 ≥3%、开盘 30 分钟以后、QQQ 同时也在跌，各 1 分。</p>
<p><b>3. 等确认</b>：1 分钟 K 线收盘重新站上 1 分钟 MA20 再进；之后跌破针底就走。</p>
<p>三条都满足的历史结果（81 次）：30 分钟内先涨 2% 的训练期 55%、检验期 44%，先跌破针底的两期都约 21%。平均要等 10 分钟，进场价比插针那一刻贵约 1%，约一半的针等不到确认，等不到就不做。</p>
<p>1 分钟 RSI、1 分钟和 5 分钟 KDJ、5 分钟布林下轨、收回 MA5、5 分钟下影收线都测过：两期结论相反或者没区别，不用看。</p>
</div>
<div class='card'><p>想事后核对某一分钟的读数，可以在终端查：</p>
<pre>cd ~/Codes/tiger-review && .venv/bin/python -m tiger_review spike SPCX --at 09:44 --date 2026-10-21</pre></div>

<h2>口径与局限</h2>
<ul class='sub'>
<li>针的定义：从最近 30 分钟高点回落，回落深度按过去 14 天平均日内振幅衡量；这一分钟创出 15 分钟新低。下影接 = 下影线过半、收在上半区的那一分钟收盘接；急跌接 = 按开盘以来 1 分钟波动折算跌速 ≥2.5 倍时直接接。两种接法去重后结果没有差别，统计合并。</li>
<li>热点日用当天看得到的痕迹代替新闻：跳空 ≥3%，或开盘以来成交量是过去 20 天同时段的 2 倍以上。你按消息选的热点票不一定都留下这种痕迹，比如 NVDA 10/20 平开、量也不大。</li>
<li>结果只看标的价格，没有算期权：同样的 +2%，当天到期虚值 Call 往往翻倍以上，−2% 则大部分权利金归零。</li>
<li>样本：{n_syms} 只票，均为高流动性科技与 AI 基建股；检验期只有 2026 年约 9 个月，高分档样本少，结论需要随新数据滚动更新。</li>
</ul>
</main></body></html>"""
    open(OUT, "w", encoding="utf-8").write(html)
    print(OUT)


if __name__ == "__main__":
    main()
