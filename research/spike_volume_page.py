"""把 spike_volume.csv 的结论画成单页 HTML（纯内嵌 SVG，不用 JS）。

用法：.venv/bin/python -I research/spike_volume_page.py
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data/research/spike_volume.csv"
OUT = Path("/Users/vock/Codes/reports/插针缩量与放量-2026-10-22.html")
CSS_SRC = Path("/Users/vock/Codes/reports/涨跌幅八成区间-2026-10-22/build.py")
css = re.search(r'css = """(.*?)"""', CSS_SRC.read_text(encoding="utf-8"), re.S).group(1)
css += ".sw2{background:var(--s2)}:root{--s2:#eb6834}@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){--s2:#d95926}}:root[data-theme=dark]{--s2:#d95926}"
esc = lambda s: html.escape(str(s))

d = pd.read_csv(D)
d["hot"] = np.where((d.gap.abs() >= 0.03) | (d.rvol >= 2), "热点日", "普通日")
d["period"] = np.where(d.day < "2026-01-01", "训练", "检验")
d["w2"] = d.res2 == "up"; d["l2"] = d.res2 == "down"
dec = d.first_half_r.isin(["up", "down"]); d["half"] = np.where(dec, (d.first_half_r == "up").astype(float), np.nan)
rng = np.random.default_rng(0)


def net(g):
    return (g.w2.mean() - g.l2.mean()) * 100


def ci(g, B=2000):
    w, l = g.w2.to_numpy(), g.l2.to_numpy(); n = len(g)
    idx = rng.integers(0, n, (B, n))
    v = (w[idx].mean(1) - l[idx].mean(1)) * 100
    return np.percentile(v, [2.5, 97.5])


PER = {"训练": "2024-10 至 2025-12", "检验": "2026 年"}
REF = {"r1": "比前一天同一时段", "r5": "比前 5 天同一时段平均"}
stats = {}
for hot in ("热点日", "普通日"):
    for ref in ("r1", "r5"):
        for per in ("训练", "检验"):
            x = d[(d.hot == hot) & (d.period == per)]
            for lab, g in (("缩量", x[x[ref] < 1]), ("放量", x[x[ref] >= 1])):
                stats[(hot, ref, per, lab)] = dict(n=len(g), up=g.w2.mean() * 100, dn=g.l2.mean() * 100, net=net(g),
                                                   ci=ci(g), half=g.half.mean() * 100, r30=g.ret30.mean() * 100)


# ---------- 图 1：热点日，缩量 vs 放量，净胜（先涨 2% 减先跌 2%）带 95% 区间 ----------
def dot_panel(hot, W=960):
    L, Rm, T, rh, B = 300, 40, 34, 30, 30
    rows = [(ref, per, lab) for ref in ("r1", "r5") for per in ("训练", "检验") for lab in ("缩量", "放量")]
    H = T + rh * len(rows) + 16 + B; lo, hi = -20, 20
    X = lambda v: L + (min(max(v, lo), hi) - lo) / (hi - lo) * (W - L - Rm)
    s = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{hot} 缩量与放量的净胜">',
         f'<text x="{L}" y="16" class="t-sub">{hot}：30 分钟内「先涨 2% 的比例 − 先跌 2% 的比例」（点 = 结果，横线 = 95% 区间）</text>']
    for g in range(lo, hi + 1, 5):
        s.append(f'<line x1="{X(g):.1f}" y1="{T - 6}" x2="{X(g):.1f}" y2="{H - B}" class="{"axis" if g == 0 else "grid"}"/>'
                 f'<text x="{X(g):.1f}" y="{H - 8}" class="t-tick" text-anchor="middle">{g:+d}</text>')
    y = T
    for k, (ref, per, lab) in enumerate(rows):
        if k and k % 4 == 0:
            y += 16
        st = stats[(hot, ref, per, lab)]; cy = y + rh / 2; col = "s1" if lab == "缩量" else "s2"
        a, b = st["ci"]
        tip = (f'{hot}·{REF[ref]}·{lab}·{PER[per]}：{st["n"]} 次，先涨 2% {st["up"]:.0f}%、先跌 2% {st["dn"]:.0f}%，'
               f'净胜 {st["net"]:+.1f}（95% 区间 {a:+.1f} 到 {b:+.1f}），先弹回半针 {st["half"]:.0f}%')
        head = f'{REF[ref]} · {PER[per]}' if lab == "缩量" else ""
        s.append(f'<g><title>{esc(tip)}</title><rect x="0" y="{y}" width="{W}" height="{rh}" fill="transparent"/>'
                 f'<text x="{L - 70}" y="{cy + 4:.1f}" class="t-val" text-anchor="end">{esc(head)}</text>'
                 f'<text x="{L - 12}" y="{cy + 4:.1f}" class="t-lab" text-anchor="end">{lab}</text>'
                 f'<line x1="{X(a):.1f}" y1="{cy}" x2="{X(b):.1f}" y2="{cy}" style="stroke:var(--{col});stroke-width:2;stroke-linecap:round"/>'
                 f'<circle cx="{X(st["net"]):.1f}" cy="{cy}" r="5" class="ring" style="fill:var(--{col});stroke:var(--surface);stroke-width:2"/>'
                 f'<text x="{X(b) + 8:.1f}" y="{cy + 4:.1f}" class="t-val">{st["net"]:+.1f}　n={st["n"]}</text></g>')
        y += rh
    s.append("</svg>")
    return "".join(s)


leg = ('<div class="legend"><span><i class="sw" style="background:var(--s1)"></i>缩量：急跌那段成交量低于对照</span>'
       '<span><i class="sw sw2"></i>放量：高于对照</span><span>0 线右边 = 先涨 2% 的多</span></div>')
fig1 = '<div class="scroll">' + dot_panel("热点日") + '</div>'
fig1b = '<div class="scroll">' + dot_panel("普通日") + '</div>'

# ---------- 图 2：热点日，比前一周细分五档，先弹回半针 ----------
bins = [0, 0.7, 1.0, 1.5, 2.5, 1e9]; labs = ["不到 0.7 倍", "0.7–1 倍", "1–1.5 倍", "1.5–2.5 倍", "2.5 倍以上"]
h = d[d.hot == "热点日"].copy(); h["b"] = pd.cut(h.r5, bins, labels=labs)
fine = h.groupby(["b", "period"], observed=True).agg(n=("half", "size"), half=("half", "mean"), up=("w2", "mean"), dn=("l2", "mean")).reset_index()
W, L, Rm, T, B = 960, 70, 40, 40, 46; H2 = 300; ph = H2 - T - B; pw = W - L - Rm
Y = lambda v: T + (1 - (v - 0.3) / 0.45) * ph
s = [f'<svg viewBox="0 0 {W} {H2}" width="100%" role="img" aria-label="热点日按比前一周的量能分五档，先弹回半针的比例">',
     f'<text x="{L}" y="16" class="t-sub">热点日：急跌那段成交量是前 5 天同一时段的几倍 → 30 分钟内先弹回一半回落的比例</text>']
for g in (0.3, 0.4, 0.5, 0.6, 0.7):
    s.append(f'<line x1="{L}" y1="{Y(g):.1f}" x2="{W - Rm}" y2="{Y(g):.1f}" class="{"ref" if g == 0.5 else "grid"}"/>'
             f'<text x="{L - 8}" y="{Y(g) + 4:.1f}" class="t-tick" text-anchor="end">{g * 100:.0f}%</text>')
step = pw / len(labs)
for k, lab in enumerate(labs):
    cx = L + step * (k + 0.5)
    s.append(f'<text x="{cx:.1f}" y="{H2 - 24}" class="t-val" text-anchor="middle">{lab}</text>')
    for per, dx, col in (("训练", -14, "s1"), ("检验", 14, "s2")):
        r = fine[(fine.b == lab) & (fine.period == per)]
        if not len(r):
            continue
        r = r.iloc[0]
        s.append(f'<g><title>{lab}·{PER[per]}：{int(r.n)} 次，先弹回半针 {r.half * 100:.0f}%，先涨 2% {r.up * 100:.0f}%、先跌 2% {r.dn * 100:.0f}%</title>'
                 f'<line x1="{cx + dx:.1f}" y1="{Y(0.5):.1f}" x2="{cx + dx:.1f}" y2="{Y(r.half):.1f}" style="stroke:var(--{col});stroke-width:2"/>'
                 f'<circle cx="{cx + dx:.1f}" cy="{Y(r.half):.1f}" r="6" style="fill:var(--{col});stroke:var(--surface);stroke-width:2"/>'
                 f'<text x="{cx + dx:.1f}" y="{Y(r.half) + (-12 if r.half >= 0.5 else 20):.1f}" class="t-val" text-anchor="middle">{r.half * 100:.0f}</text></g>')
s.append(f'<text x="{L + 4}" y="{H2 - 6}" class="t-tick">缩量 ←　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　　→ 放量</text></svg>')
fig2 = '<div class="scroll">' + "".join(s) + "</div>"
leg2 = ('<div class="legend"><span><i class="sw" style="background:var(--s1)"></i>2024-10 至 2025-12（训练期）</span>'
        '<span><i class="sw sw2"></i>2026 年（检验期）</span><span><i class="ln" style="border-top:1px dashed var(--ref)"></i>50% = 硬币</span></div>')

# ---------- 表格 ----------
trs = []
for (hot, ref, per, lab), st in stats.items():
    trs.append(f'<tr><td>{hot}</td><td>{REF[ref]}</td><td>{PER[per]}</td><td>{lab}</td><td>{st["n"]}</td>'
               f'<td>{st["up"]:.1f}%</td><td>{st["dn"]:.1f}%</td><td>{st["net"]:+.1f}</td><td>{st["ci"][0]:+.1f} ~ {st["ci"][1]:+.1f}</td>'
               f'<td>{st["half"]:.1f}%</td><td>{st["r30"]:+.2f}%</td></tr>')
table = ('<div class="tw"><table><tr><th>日子</th><th>对照</th><th>时期</th><th>急跌段</th><th>次数</th><th>先涨 2%</th><th>先跌 2%</th>'
         '<th>净胜</th><th>净胜 95% 区间</th><th>先弹回半针</th><th>30 分钟后平均</th></tr>' + "".join(trs) + '</table></div>')
ftr = []
for _, r in fine.iterrows():
    ftr.append(f'<tr><td>热点日</td><td>{r.b}</td><td>{PER[r.period]}</td><td>{int(r.n)}</td><td>{r.up * 100:.1f}%</td><td>{r.dn * 100:.1f}%</td><td>{r.half * 100:.1f}%</td></tr>')
table2 = ('<div class="tw"><table><tr><th>日子</th><th>比前 5 天同时段</th><th>时期</th><th>次数</th><th>先涨 2%</th><th>先跌 2%</th><th>先弹回半针</th></tr>'
          + "".join(ftr) + '</table></div>')


def F(b, per):
    return float(fine[(fine.b == b) & (fine.period == per)].half.iloc[0] * 100)


nneg = sum(v["net"] < 0 for k, v in stats.items() if k[0] == "普通日")


def S(*k):
    return stats[k]


a1, b1 = S("热点日", "r1", "训练", "缩量"), S("热点日", "r1", "训练", "放量")
a2, b2 = S("热点日", "r1", "检验", "缩量"), S("热点日", "r1", "检验", "放量")
c1, e1 = S("热点日", "r5", "训练", "缩量"), S("热点日", "r5", "训练", "放量")
c2, e2 = S("热点日", "r5", "检验", "缩量"), S("热点日", "r5", "检验", "放量")
maxdiff = max(abs(a1['net'] - b1['net']), abs(a2['net'] - b2['net']), abs(c1['net'] - e1['net']), abs(c2['net'] - e2['net']))
nshr = int((d[d.hot == "热点日"].r1 < 1).sum()); nhot = int((d.hot == "热点日").sum())


def card(title, body, look, concl, use):
    return (f'<div class="card"><h2 style="margin-top:0">{esc(title)}</h2>{body}'
            f'<div class="read"><div><b>看什么：</b>{look}</div><div><b>结论：</b>{concl}</div><div><b>怎么用：</b>{use}</div></div></div>')


kpis = f'''<div class="kpis">
<div class="kpi"><div class="l">热点日比前一天：缩量 − 放量 的净胜差</div><div class="v">{a1["net"] - b1["net"]:+.0f} / {a2["net"] - b2["net"]:+.0f}</div><div class="d">训练期 / 2026 年，方向反了，都在噪音里</div></div>
<div class="kpi"><div class="l">热点日比前一周：缩量 − 放量 的净胜差</div><div class="v">{c1["net"] - e1["net"]:+.0f} / {c2["net"] - e2["net"]:+.0f}</div><div class="d">两期都略偏缩量，但 95% 区间都跨过 0</div></div>
<div class="kpi"><div class="l">热点日里急跌段比前一天缩量的</div><div class="v">{nshr / nhot * 100:.0f}%</div><div class="d">热点日本来就放量，缩量急跌少见（{nshr} / {nhot} 次）</div></div>
</div>'''

page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>插针缩量与放量</title><style>{css}</style></head><body><div class="wrap">
<h1>缩量急跌的针，是不是比放量急跌的好接</h1>
<p class="lead">65 只热门股 2024-10 到 2026-10 的 1 分钟线，{len(d):,} 根回落 ≥0.5 倍日均振幅的下影针（每只票每天取第一根）。急跌那段 = 从 30 分钟内的高点跌到针底；它的成交量跟<b>前一天同一时段</b>、<b>前 5 天同一时段平均</b>比，低于 1 倍算缩量。<b>结论：不是。热点日缩量和放量的净胜差最多 {maxdiff:.0f} 个点，95% 区间全都跨过 0；比前一天的结果在 2026 年还反过来了，不能当接针条件。</b></p>
{kpis}
{card('图 1 · 热点日：缩量和放量，接针结果几乎一样', fig1 + leg,
      '针那一分钟收盘进场，30 分钟内先涨 2% 的比例减去先跌 2% 的比例，叫「净胜」。点是结果，横线是重抽 2000 次得到的 95% 区间。上四行比前一天，下四行比前一周，每组再分训练期和 2026 年。',
      f'比前一天：训练期缩量好 {a1["net"] - b1["net"]:.0f} 个点，2026 年反过来差 {abs(a2["net"] - b2["net"]):.0f} 个点。比前一周：两期都是缩量略好（{c1["net"] - e1["net"]:+.0f}、{c2["net"] - e2["net"]:+.0f}），但所有横线都跨过 0，蓝点和橙点的横线大面积重叠。2026 年不管缩量放量，净胜都是负的。',
      '急跌段量能不用看。决定接不接的还是原来那几条：热点日、回落 ≥1 倍日均振幅、打分。')}
{card('图 2 · 细分五档：没有「越缩越好」，也没有「越放越差」', fig2 + leg2,
      '只看热点日，按急跌那段成交量是前 5 天同一时段的几倍分五档，纵轴是 30 分钟内「先弹回一半回落、还是先再跌同样幅度」里先弹回的比例。50% 是硬币。',
      f'最缩的一档（不到 0.7 倍）训练期 {F("不到 0.7 倍", "训练"):.0f}%、2026 年 {F("不到 0.7 倍", "检验"):.0f}%，正好反过来，样本也只有 {int(fine[fine.b == "不到 0.7 倍"].n.min())}–{int(fine[fine.b == "不到 0.7 倍"].n.max())} 次。唯一两期都偏低的是「放量 1.5–2.5 倍」（{F("1.5–2.5 倍", "训练"):.0f}%、{F("1.5–2.5 倍", "检验"):.0f}%），但放大到 2.5 倍以上又回到 {F("2.5 倍以上", "检验"):.0f}%–{F("2.5 倍以上", "训练"):.0f}%，形状不连贯，更像碰巧。',
      '别因为「放量一倍半到两倍半」就躲开，也别因为「缩量」就加码；要验证得等 2026 年样本翻倍以后重跑。')}
{card('附 · 普通日：缩量放量都一样，都是先跌的多', fig1b + leg,
      '同样的算法，只看没有跳空 3%、开盘以来量能不到 2 倍的普通日子。',
      f'八组里 {nneg} 组净胜为负，缩量和放量的点挨在一起。普通日的针本来就不值得接，量能救不了。',
      '普通日的针不接，这一条不因量能改变。')}
<div class="card"><h2 style="margin-top:0">全部数字</h2><details open><summary>展开 / 收起</summary>{table}{table2}</details></div>
<ul class="foot">
<li>热点日 = 跳空 ≥3% 或开盘以来累计量 ≥2 倍过去 20 天同一时刻，和插针量化报告同一口径。训练期 2024-10 至 2025-12，检验期 2026 年。</li>
<li>急跌段成交量去掉了 16:00 那根（含收盘竞价）；前一天或前 5 天同一时段没成交的事件剔除。</li>
<li>「先涨 2% / 先跌 2%」看 30 分钟内谁先碰到，两个都没碰到的不算赢也不算输，所以两者加起来不到 100%。</li>
<li>回落 ≥1 倍日均振幅的热点深针按量能再分，每格只剩几十次，这里不下结论。</li>
<li>阈值（1 倍分界、五档切点）是第一版设定，没有调参。</li>
</ul>
</div></body></html>'''
OUT.write_text(page, encoding="utf-8")
print(OUT)
