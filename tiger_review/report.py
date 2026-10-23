"""把分析结果渲染成单文件 HTML（无 JS，内嵌 SVG，深浅色自适应）。"""
from __future__ import annotations

import html
from datetime import datetime

COMP = [("direction", "方向", "c1"), ("time", "时间损耗", "c2"), ("vol", "波动率", "c3"),
        ("fees", "费用", "c4")]
COMP_TXT = {"direction": "方向", "time": "时间损耗", "vol": "波动率", "fees": "费用", "model": "模型误差"}


def esc(x) -> str:
    return html.escape(str(x))


def usd(x, sign: bool = True) -> str:
    if x is None:
        return "—"
    s = f"${abs(x):,.0f}"
    if not sign:
        return s
    return ("+" if x > 0 else "−" if x < 0 else "") + s


def pct(x, sign: bool = True, digits: int = 0) -> str:
    if x is None:
        return "—"
    v = f"{abs(x) * 100:.{digits}f}%"
    return (("+" if x > 0 else "−" if x < 0 else "") if sign else "") + v


def pn(x) -> str:
    """盈亏的样式类：红盈绿亏。"""
    return "pos" if (x or 0) > 0 else "neg" if (x or 0) < 0 else ""


# ------------------------------------------------------------------ 图
def bars(rows: list[tuple[str, float, str]], width: int = 640) -> str:
    """横向发散条：零线居中，右红（盈）左绿（亏），条尾标数。"""
    if not rows:
        return ""
    lab_w, pad, row_h = 190, 64, 30
    plot = width - lab_w - pad * 2
    m = max(abs(v) for _, v, _ in rows) or 1
    zero = lab_w + pad + plot / 2
    h = row_h * len(rows) + 8
    out = [f'<svg viewBox="0 0 {width} {h}" class="chart" role="img">']
    out.append(f'<line x1="{zero}" y1="0" x2="{zero}" y2="{h}" class="axis"/>')
    for i, (lab, v, tip) in enumerate(rows):
        y = 4 + i * row_h
        w = abs(v) / m * (plot / 2)
        x = zero if v >= 0 else zero - w
        cls = "barpos" if v >= 0 else "barneg"
        out.append(f'<g><title>{esc(tip)}</title>'
                   f'<text x="{lab_w - 6}" y="{y + 19}" class="lab" text-anchor="end">{esc(lab)}</text>'
                   f'<rect x="{x:.1f}" y="{y + 6}" width="{max(w, 1.5):.1f}" height="16" rx="4" class="{cls}"/>'
                   f'<text x="{(zero + w + 6) if v >= 0 else (zero - w - 6):.1f}" y="{y + 19}" '
                   f'class="val" text-anchor="{"start" if v >= 0 else "end"}">{usd(v)}</text></g>')
    out.append("</svg>")
    return "".join(out)


def decomp_bar(d: dict | None, width: int = 640, height: int = 34) -> str:
    """一条拆解条：正项从零线往右叠，负项往左叠；下方直接标注各项。"""
    if not d:
        return '<p class="muted">缺行情，未拆解。</p>'
    items = [(k, name, cls, d.get(k, 0.0)) for k, name, cls in COMP]
    pos = sum(v for *_, v in items if v > 0)
    neg = -sum(v for *_, v in items if v < 0)
    m = max(pos, neg) or 1
    pad = 8
    half = (width - pad * 2) / 2
    zero = pad + half
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    xr, xl = zero, zero
    for k, name, cls, v in items:
        if abs(v) < 0.5:
            continue
        w = abs(v) / m * half
        if v > 0:
            x, xr = xr, xr + w
        else:
            xl -= w
            x = xl
        out.append(f'<rect x="{x + 1:.1f}" y="6" width="{max(w - 2, 1):.1f}" height="20" rx="4" '
                   f'class="{cls}"><title>{name} {usd(v)}</title></rect>')
    out.append(f'<line x1="{zero}" y1="0" x2="{zero}" y2="{height}" class="axis"/></svg>')
    legend = "".join(f'<span class="lg"><i class="sw {cls}"></i>{name} <b class="{pn(d.get(k))}">'
                     f'{usd(d.get(k, 0))}</b></span>' for k, name, cls in COMP)
    other = d.get("model", 0)
    if abs(other) >= 1:
        legend += f'<span class="lg muted">模型误差 {usd(other)}</span>'
    return "".join(out) + f'<div class="legend">{legend}</div>'


# ------------------------------------------------------------------ 文案
def _culprit(d: dict | None) -> str | None:
    if not d:
        return None
    neg = {k: v for k, v in d.items() if k in ("direction", "time", "vol") and v < 0}
    return min(neg, key=neg.get) if neg else None


def _name(r: dict) -> str:
    t = datetime.fromisoformat(r["open_ts"])
    return f"{r.get('main_leg') or r['underlying']}（{t:%m/%d %H:%M} 开）"


def small_findings(recs: list[dict]) -> list[tuple[float, str, str]]:
    """样本少时逐笔归纳共同点：[(金额影响, 说明, 诊断代码)]。"""
    closed = [r for r in recs if r["status"] == "closed"]
    out: list[tuple[float, str, str]] = []
    judged = [r for r in closed if r.get("move_horizon") is not None]
    if len(judged) >= 3:
        right = sum(1 for r in judged if r.get("dir_right_horizon"))
        wrong = sum(1 for r in judged if r.get("dir_right_horizon") is False)
        flat = len(judged) - right - wrong
        wins = sum(1 for r in judged if r["pnl"] > 0)
        if wrong == 0 and wins < len(judged):
            lost = sum(r["pnl"] for r in judged if r["pnl"] < 0)
            out.append((float("inf"),
                        f"到期为止没有一笔方向看反：{right} 笔看对、{flat} 笔标的基本没动，但只有 {wins} 笔赚钱。"
                        f"亏掉的 <b class='neg'>{usd(lost)}</b> 不是看反了方向，而是行权价和进出时点。", "view_ok"))
    shaken = [r for r in closed if r["pnl"] < 0 and r.get("dir_right_horizon")
              and (r.get("exit_adverse") or 0) >= 0.85]
    if shaken:
        names = "、".join(_name(r) for r in shaken)
        lost = sum(r["pnl"] for r in shaken)
        out.append((abs(lost),
                    ("方向看对却亏的单，都走在标的前后一小时最不利的位置：" if len(shaken) > 1
                     else "方向看对却亏的那笔，走在标的前后一小时最不利的位置：") + f"{esc(names)}，"
                    f"{'合计 ' if len(shaken) > 1 else ''}"
                    f"<b class='neg'>{usd(lost)}</b>。"
                    + (f"离场后标的照你的方向走了（{'、'.join(pct(r['post_move'], digits=1) for r in shaken)}）。"
                       if all(r.get("post_move") is not None for r in shaken) else ""), "shaken"))
    early = [r for r in closed if r["pnl"] > 0 and (r.get("post_missed") or 0) > 0.5 * r["pnl"]]
    if early:
        miss = sum(r["post_missed"] for r in early)
        r0 = max(early, key=lambda r: r["post_missed"])
        cap = f"只拿到持仓期间最大浮盈的 {pct(r0['capture'], False)}，" if r0.get("capture") else ""
        out.append((0.5 * miss, f"赚钱的单走早了：{esc(_name(r0))} 赚 <b class='pos'>{usd(r0['pnl'])}</b>，{cap}"
                          f"{esc(r0.get('post_label', '之后'))}标的又走了 {pct(r0['post_move'], digits=1)}，"
                          f"拿着估计还能多 <b>{usd(r0['post_missed'])}</b>。", "early"))
    rt = [r for r in closed if r["pnl"] < 0 and (r.get("mfe") or 0) > max(0.2 * abs(r["pnl"]), 50)]
    wins = [r for r in closed if r["pnl"] > 0 and r.get("capture") is not None]
    if rt:
        det = "；".join(f"{esc(_name(r))} 曾浮盈约 {usd(r['mfe'])}，最后 {usd(r['pnl'])}" for r in rt)
        contrast = ("；而赚钱的那笔在标的还在顺向走时就全部卖了" if len(wins) == 1
                    and (wins[0].get("post_missed") or 0) > 0 else "")
        out.append((sum(r["mfe"] - r["pnl"] for r in rt) * 0.5,
                    f"浮盈转亏才走的 {len(rt)} 笔：{det}{contrast}。"
                    + ("赚着的时候走得快，赚过又回吐的反而没走。" if contrast else ""),
                    "round_trip"))
    never = [r for r in closed if r.get("strike_reached") is False and r["pnl"] < 0]
    hit = [r for r in closed if r.get("strike_reached")]
    if never:
        lost = sum(r["pnl"] for r in never)
        det = "；".join(f"{esc(_name(r))} 行权价离现价 {pct(abs(r['need_move']), False, 2)}，"
                       f"到期前标的最远只走了 {pct(abs(r['best_move']), False, 2)}" for r in never)
        tail = (f"。另外 {len(hit)} 笔的行权价都被碰到了" if hit else "")
        out.append((abs(lost), f"行权价从头到尾没被碰到的 {len(never)} 笔，合计 <b class='neg'>{usd(lost)}</b>："
                               f"{det}{tail}。", "strike_far"))
    up = [r for r in closed if r.get("tags", {}).get("亏后仓位")]
    for r in up:
        ctx = ""
        if r.get("pre2h") is not None and r.get("hold_range"):
            ctx = (f"；进场前标的从 {r['pre2h_from']} 起已朝你的方向走了 {pct(r['pre2h'], digits=1)}，"
                   f"持有期间只在 {r['hold_range'][0]:g} 至 {r['hold_range'][1]:g} 之间来回")
        out.append((abs(min(r["pnl"], 0)) or 1,
                    f"同标的亏完再开，仓位放大到上一笔的 {r['size_vs_prev']:.1f} 倍：{esc(_name(r))}，结果 "
                    f"<b class='{pn(r['pnl'])}'>{usd(r['pnl'])}</b>{ctx}。", "size_up"))
    adds = [a for r in closed for a in r.get("add_list", []) if a["below_cost"]]
    if adds:
        good = [a for a in adds if a["pnl"] > 0]
        tot = sum(a["pnl"] for a in adds)
        out.append((abs(tot) * 0.5,
                    f"在期权跌价时加仓 {len(adds)} 次（标的往反方向走时加），这几次加仓本身 {len(good)} 次赚、"
                    f"{len(adds) - len(good)} 次亏，合计 <b class='{pn(tot)}'>{usd(tot)}</b>。", "avg_down"))
    seen_sets = []
    for dim in ("日内位置", "进场盘面", "期限", "虚实", "期权贵贱", "离场位置", "时段"):
        groups: dict[str, list] = {}
        for r in closed:
            v = (r.get("tags") or {}).get(dim)
            if v:
                groups.setdefault(v, []).append(r)
        for v, g in groups.items():
            if len(g) < 2 or len(g) == len(closed):
                continue
            signs = {r["pnl"] > 0 for r in g}
            if len(signs) != 1:
                continue
            ids = frozenset(r["cid"] for r in g)
            if ids in seen_sets:
                continue
            seen_sets.append(ids)
            tot = sum(r["pnl"] for r in g)
            word = "全赚" if tot > 0 else "全亏"
            out.append((abs(tot) * 0.4,
                        f"苗头（样本 {len(g)} 笔）：「{esc(v)}」的 {len(g)} 笔{word}，合计 "
                        f"<b class='{pn(tot)}'>{usd(tot)}</b>：" + "、".join(esc(_name(r)) for r in g) + "。",
                        "signal_win" if tot > 0 else "signal_loss"))
    fees = sum(r["fees"] for r in closed)
    net = sum(r["pnl"] for r in closed)
    if net < 0 and fees > 0.2 * abs(net):
        out.append((fees, f"手续费 {usd(fees, False)}，相当于净亏的 {pct(fees / abs(net), False)}："
                          "便宜的当天到期合约张数多，费用按张收。", "fees"))
    out.sort(key=lambda x: -x[0])
    return out


SMALL_NEXT = {
    "view_ok": "观点不用改，改工具和时点：行权价选在标的够得着的那一档，进场前写下标的到哪算看错，离场只看这个价位。",
    "round_trip": "进场前写下两条：标的到哪算看错、浮盈到多少先落袋一部分，离场按这两条走，对照这两天的四笔。",
    "strike_far": "当天到期的单，行权价先对照标的当天已走出的振幅，选它够得着的那一档，对照这几笔。",
    "shaken": "进场前先写下标的到哪个价位算看错，离场只看这个价位，不看期权浮亏比例；下一批单对照这两笔。",
    "early": "赚钱时留一部分，等标的破位或到期再走，对照一次全部卖出的结果。",
    "size_up": "亏完之后的下一笔不放大，对照这一笔。",
    "avg_down": "加仓只加在标的往你方向走的时候，对照摊平的这几笔。",
    "fees": "同样的观点，少买张数、选权利金高一点的合约，看费用占比能降多少。",
}


def spike_text(sp: dict | None) -> str:
    """插针读数 + 历史同类胜算，一句话。"""
    if not sp:
        return ""
    up = sp.get("kind") == "冲高"
    q = f"，QQQ 同期 {pct(sp['qqq_leg'], digits=2)}" if sp.get("qqq_leg") is not None else ""
    move, wick, where = ("冲高", "上影", "低点") if up else ("回落", "下影", "高点")
    s = (f"{'冲高' if up else '插针'}读数：从 {sp['peak_t']} {where}{move} {pct(sp['pb'], False, 2)}，"
         f"是日均振幅的 {sp['pb_atr']:.2f} 倍（{sp['depth'].replace('回落', move)}），相对开盘 "
         f"{pct(sp['day_move'], digits=2)}，开盘后第 {sp['minute']} 分钟{q}，这一分钟{wick} "
         f"{pct(sp['wick'], False)}；打分 {sp['score']}/4。")
    o = (sp.get("odds") or {}).get("all")
    if o:
        good, bad = ("回落 2%", "再涨 2%") if up else ("弹 2%", "跌 2%")
        s += (f"历史同类 {o['n']} 次：30 分钟内{good}占 {pct(o['up2_30'], False)}、{bad}占 "
              f"{pct(o['down2_30'], False)}，对你有利方向平均 {pct(o['ret30'], digits=2)}。")
    if up:
        s += "冲高买 Put 在回测里任何打分都没有稳定优势。"
    return s


def trade_line(r: dict) -> str:
    """一笔交易的逻辑链：选票前提 → 插针读数 → 工具 → 过程 → 结果来源 → 离场。"""
    t = datetime.fromisoformat(r["open_ts"])
    up = r.get("bias") == "看涨"
    note = r.get("note") or {}
    s = f"<b>{esc(r.get('main_leg') or r['underlying'])}</b>：{t:%H:%M} 进场，看{'涨' if up else '跌'}。"
    if note.get("选票前提"):
        s += f"选票前提：{esc(note['选票前提'])}。"
    if note.get("进场逻辑"):
        s += f"进场逻辑：{esc(note['进场逻辑'])}。"
    s += esc(spike_text(r.get("spike")))
    tool = [f"{r.get('dte_open')} 天到期" if r.get("dte_open") else "当天到期"]
    if r.get("delta_main") is not None:
        tool.append(f"Δ{abs(r['delta_main']):.2f}")
    if r.get("iv_hv"):
        tool.append(f"隐波是实际波动的 {r['iv_hv']:.1f} 倍")
    s += f"工具：{esc(r.get('structure'))}，" + "，".join(tool) + "。"
    if r["status"] == "closed":
        hold_min = r["hold_days"] * 24 * 60
        dur = f"{hold_min:.0f} 分钟" if hold_min < 600 else f"{r['hold_days']:.1f} 天"
        mv = f"标的 {pct(r['move_hold'], digits=1)}，" if r.get("move_hold") is not None else ""
        s += (f"持有 {dur}，{mv}结果 <b class='{pn(r['pnl'])}'>{usd(r['pnl'])}</b>"
              + (f"（{pct(r['ret'])}）" if r.get("ret") is not None else "") + "。")
        d = r.get("decomp")
        if d:
            s += f"方向 {usd(d.get('direction'))}，时间 {usd(d.get('time'))}，隐波 {usd(d.get('vol'))}。"
        if r.get("post_move") is not None:
            pm = r["post_missed"]
            verdict = (("走得对" if pm < 0 else "卖早了") if r["pnl"] > 0 else ("砍对了" if pm < 0 else "砍在了低点"))
            s += (f"离场后{esc(r.get('post_label', ''))}标的 {pct(r['post_move'], digits=1)}，"
                  f"拿着估计 {usd(pm)}，{verdict}。")
    return s


def headline(res: dict, recs: list[dict] | None = None, day: bool = False) -> dict:
    """第一屏：钱从哪来、钱漏在哪、一个机制、下一步验证一件事。"""
    ov = res.get("overall") or {}
    lines = []
    if day and recs:
        today = [trade_line(r) for r in recs[:3]]
        cum = (f"累计 {ov['n']} 笔，{ov['wins']} 赚 {ov['n'] - ov['wins']} 亏，净 "
               f"<b class='{pn(ov['pnl'])}'>{usd(ov['pnl'])}</b>。") if ov.get("n") else ""
        fs = small_findings(res.get("_all_recs", recs)) if ov.get("n", 0) < 8 else []
        return {"lines": today + ([cum] if cum and len(today) < 3 else []), "next": None, "overall": ov,
                "all": [f for _, f, _ in fs]}
    if recs is not None and 0 < ov.get("n", 0) < 8:
        fs = small_findings(recs)
        wins = ov["wins"]
        best = max((r for r in recs if r["status"] == "closed"), key=lambda r: r["pnl"])
        head = (f"<b>{ov['n']} 笔，{wins} 赚 {ov['n'] - wins} 亏，净 <span class='{pn(ov['pnl'])}'>"
                f"{usd(ov['pnl'])}</span></b>。样本太少，不按打法分组，下面是这几笔的共同点。")
        lines = [head] + [f for _, f, _ in fs[:2]]
        nxt = SMALL_NEXT.get(fs[0][2]) if fs else None
        return {"lines": lines, "next": nxt, "overall": ov, "all": [f for _, f, _ in fs]}
    e = res["edges"][0] if res["edges"] else None
    l = res["leaks"][0] if res["leaks"] else None
    if e:
        lines.append(f"<b>钱从这里来</b>：{esc(e['label'])}，{e['n']} 笔赚 {e['wins']} 笔，净 "
                     f"<b class='pos'>{usd(e['pnl'])}</b>。{esc(e['why'])}")
    elif ov.get("n"):
        share = res["behavior"].get("top3_share")
        extra = f"盈利集中在少数几笔：最赚的 3 笔占全部盈利的 {pct(share, False)}" if share else "赚钱的笔分散、不成类"
        lines.append(f"<b>钱从这里来</b>：没有哪类打法在去掉最大一笔后还赚钱；{extra}。")
    if l:
        lines.append(f"<b>钱漏在这里</b>：{esc(l['label'])}，{l['n']} 笔亏 {l['n'] - l['wins']} 笔，净 "
                     f"<b class='neg'>{usd(l['pnl'])}</b>。{esc(l['why'])}")
    mech = mechanism(res)
    if mech:
        lines.append(f"<b>关键机制</b>：{mech}")
    nxt = None
    if l and l.get("diag"):
        action = {
            "eaten_by_vol": "同样的观点，改买更实值或更远月的合约，少为隐波付钱，看波动率这一项能不能收窄。",
            "eaten_by_time": "同样的观点，到期日拉远一档或缩短持有，看时间损耗占比能不能降下来。",
            "timing": "观点多半是对的，这类单换进场方式（等回踩或确认再进、给足期限），对比下一批结果。",
            "thesis": "这类观点本身兑现率低，先停掉，攒够样本再决定要不要恢复。",
            "vol_hit": "这类单进场时隐波偏贵，换更实值的合约或等隐波回落再进。",
            "hold_losers": "这类单亏损时拖得比赚钱时久，进场前先写下「什么情况算看错」，到了就走。",
            "early_exit": "这类单止盈偏早，试一次分批离场，留一部分跟趋势。",
        }
        for code in l["diag"]:
            if code in action:
                nxt = f"针对「{esc(l['label'])}」：{action[code]}"
                break
    return {"lines": lines[:3], "next": nxt, "overall": ov}


def mechanism(res: dict) -> str | None:
    cands = []
    q = res["quadrant"].get("dir_right_hold", {})
    rh = q.get("对亏")
    if rh and rh["n"] >= 2:
        c = _culprit(rh.get("decomp"))
        right_n = sum(q[k]["n"] for k in ("对赚", "对亏") if k in q)
        tot = sum(v["n"] for v in q.values()) or 1
        win_n = sum(q[k]["n"] for k in ("对赚", "错赚") if k in q)
        if c in ("time", "vol"):
            cands.append((abs(rh["pnl"]),
                          f"持有期间方向看对 {right_n}/{tot} 笔，最终赚钱只有 {win_n} 笔；方向对却亏的 {rh['n']} 笔共 "
                          f"<b class='neg'>{usd(rh['pnl'])}</b>，主要被{COMP_TXT[c]}吃掉。"))
        else:
            cands.append((abs(rh["pnl"]),
                          f"方向看对却亏钱的有 {rh['n']} 笔，共 <b class='neg'>{usd(rh['pnl'])}</b>：标的走对了，但离场时点没跟上。"))
    ex = res["exit"]
    ov = res.get("overall") or {}
    wins_pnl = ov.get("avg_win", 0) * ov.get("wins", 0)
    if ex.get("missed_after_win") and ex["missed_after_win"] > 0 and wins_pnl > 0 \
            and ex["missed_after_win"] > 0.3 * wins_pnl:
        cands.append((ex["missed_after_win"],
                      f"赚钱的单离场后拿到合约到期（当天到期的到收盘），估计还能多赚 <b>{usd(ex['missed_after_win'])}</b>"
                      f"（已赚 {usd(wins_pnl)}）：止盈偏早。"))
    if ex.get("expired_n") and ex.get("expired_pnl", 0) < 0:
        cands.append((abs(ex["expired_pnl"]),
                      f"{ex['expired_n']} 笔拿到到期作废，合计 <b class='neg'>{usd(ex['expired_pnl'])}</b>。"))
    hw, hl = ex.get("hold_win"), ex.get("hold_loss")
    beh = res["behavior"].get("前一笔", {})
    rv = beh.get("亏完2小时内再开")
    if rv and rv["n"] >= 2 and rv["pnl"] < 0:
        cands.append((abs(rv["pnl"]),
                      f"亏完 2 小时内马上再开的 {rv['n']} 笔，胜率 {pct(rv['win_rate'], False)}，净 "
                      f"<b class='neg'>{usd(rv['pnl'])}</b>。"))
    if hw is not None and hl is not None and hl > 2 * max(hw, 0.05):
        cands.append((0.5 * abs(ov.get("avg_loss", 0)) * max(ov.get("n", 0) - ov.get("wins", 0), 0),
                      f"亏钱的单中位拿 {__import__('tiger_review.patterns', fromlist=['fmt_hold']).fmt_hold(hl)}，"
                      f"赚钱的只拿 {__import__('tiger_review.patterns', fromlist=['fmt_hold']).fmt_hold(hw)}：赚了快走、亏了拖着。"))
    if not cands:
        return None
    return max(cands, key=lambda x: x[0])[1]


# ------------------------------------------------------------------ 页面
CSS = """
:root{--bg:#f9f9f7;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--mut:#898781;--grid:#e1e0d9;
--axis:#c3c2b7;--ring:rgba(11,11,11,.10);--pos:#d03b3b;--neg:#007a00;--barpos:#e34948;--barneg:#008300;
--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--c4:#eda100;--chip:#f0efec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d0d0d;--card:#1a1a19;--ink:#fff;
--ink2:#c3c2b7;--mut:#898781;--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--pos:#ff7b7b;
--neg:#3ccf3c;--barpos:#e66767;--barneg:#0ca30c;--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--chip:#2c2c2a}}
:root[data-theme="dark"]{--bg:#0d0d0d;--card:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--mut:#898781;--grid:#2c2c2a;
--axis:#383835;--ring:rgba(255,255,255,.10);--pos:#ff7b7b;--neg:#3ccf3c;--barpos:#e66767;--barneg:#0ca30c;
--c1:#3987e5;--c2:#d95926;--c3:#199e70;--c4:#c98500;--chip:#2c2c2a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.65 -apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
main{max-width:880px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 10px}h3{font-size:15px;margin:18px 0 6px}
.sub{color:var(--ink2);font-size:13px}.muted{color:var(--mut)}.pos{color:var(--pos)}.neg{color:var(--neg)}
.card{background:var(--card);border:1px solid var(--ring);border-radius:12px;padding:14px 16px;margin:10px 0}
.hero p{margin:6px 0}.next{border-left:3px solid var(--c1);padding-left:10px;margin-top:10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--ring);border-radius:10px;padding:8px 12px}
.kpi b{display:block;font-size:20px}.kpi span{font-size:12px;color:var(--ink2)}
.chart{width:100%;height:auto;display:block}.axis{stroke:var(--axis);stroke-width:1}
.lab{fill:var(--ink2);font-size:12px}.val{fill:var(--ink);font-size:12px}
.barpos{fill:var(--barpos)}.barneg{fill:var(--barneg)}.c1{fill:var(--c1);background:var(--c1)}
.c2{fill:var(--c2);background:var(--c2)}.c3{fill:var(--c3);background:var(--c3)}.c4{fill:var(--c4);background:var(--c4)}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;margin-top:4px}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:0}
.quad{display:grid;grid-template-columns:1fr 1fr;gap:8px}.quad .card{margin:0}
.quad b{font-size:18px}.pat{margin:8px 0}.pat .why{color:var(--ink2);font-size:14px}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:0 7px;margin:2px 4px 2px 0;font-size:12px;color:var(--ink2)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:5px 6px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}th{color:var(--ink2);font-weight:600}
.tw{overflow-x:auto}details{margin:6px 0}summary{cursor:pointer;color:var(--ink2)}
.trade h3{margin:0 0 4px;font-size:15px}.trade .row{margin:3px 0;font-size:14px}.trade .k{color:var(--mut);margin-right:6px}
.ev{font-size:13px;color:var(--ink2);margin:0;padding-left:18px}
pre{white-space:pre-wrap;word-break:break-all;font-size:12px;background:var(--chip);padding:8px 10px;border-radius:8px}
@media (max-width:560px){.quad{grid-template-columns:1fr}body{font-size:14px}}
"""


def _stat_row(st: dict) -> str:
    d = st.get("decomp") or {}
    return (f"<tr><td>{esc(st.get('value', st.get('label')))}</td><td>{st['n']}</td>"
            f"<td>{pct(st['win_rate'], False)}</td><td class='{pn(st['pnl'])}'>{usd(st['pnl'])}</td>"
            f"<td class='{pn(st['avg'])}'>{usd(st['avg'])}</td>"
            f"<td>{usd(d.get('direction')) if d else '—'}</td><td>{usd(d.get('time')) if d else '—'}</td>"
            f"<td>{usd(d.get('vol')) if d else '—'}</td>"
            f"<td>{pct(st['dir_right_horizon'], False) if st.get('dir_right_horizon') is not None else '—'}</td></tr>")


TABLE_HEAD = ("<tr><th>分组</th><th>笔数</th><th>胜率</th><th>净盈亏</th><th>每笔</th><th>方向</th>"
              "<th>时间</th><th>波动率</th><th>到期看对率</th></tr>")


def _trade_card(r: dict) -> str:
    title = (f"{esc(r['underlying'])} · {esc(r.get('main_leg', ''))} · {esc(r.get('structure'))} · "
             f"<span class='{pn(r['pnl'])}'>{usd(r['pnl'])}</span>"
             + (f" <span class='muted'>({pct(r['ret'])})</span>" if r.get("ret") is not None else ""))
    ctx = []
    t0 = datetime.fromisoformat(r["open_ts"])
    ctx.append(f"{t0:%m/%d %H:%M} 开仓")
    if r.get("S_open"):
        ctx.append(f"标的 {r['S_open']:.2f}")

    if r.get("range_pos") is not None:
        ctx.append(f"处在当天区间 {pct(r['range_pos'], False)} 位置")
    if r.get("iv_open"):
        hv = f" / 实际波动 {pct(r['hv20'], False)}" if r.get("hv20") else ""
        ctx.append(f"隐波 {pct(r['iv_open'], False)}{hv}")
    if r.get("delta_main") is not None:
        ctx.append(f"Delta {r['delta_main']:+.2f}")
    if r.get("dte_open") is not None:
        ctx.append(f"{r['dte_open']} 天到期")
    if r.get("pre5") is not None:
        ctx.append(f"背景：前 5 天 {pct(r['pre5'], digits=1)}")
    ev = "".join(f"<li>{datetime.fromisoformat(e['ts']):%m/%d %H:%M} {esc(e['type'])}："
                 f"{esc('；'.join(e['legs']))}（{usd(e['cash'])}）</li>" for e in r["events"])
    from .patterns import fmt_hold
    proc = [f"持有 {fmt_hold(r['hold_days'])}"]
    if r.get("mfe") is not None:
        if r["mfe"] > 0:
            proc.append(f"期间最大浮盈约 {usd(r['mfe'])}、最大浮亏约 {usd(r['mae'])}")
        else:
            proc.append(f"全程没有浮盈（最好时 {usd(r['mfe'])}），最大浮亏约 {usd(r['mae'])}")
    if r.get("exit_adverse") is not None:
        lo, hi = r["exit_window"]
        where = ("最不利" if r["exit_adverse"] >= 0.85 else "最有利" if r["exit_adverse"] <= 0.15 else "中段")
        proc.append(f"离场时标的处在前后 1 小时（{lo:g} 至 {hi:g}）的{where}位置")
    if r.get("capture") is not None:
        proc.append(f"拿到最大浮盈的 {pct(r['capture'], False)}")
    elif r["pnl"] < 0 and (r.get("mfe") or 0) > max(abs(r["pnl"]) * 0.1, 20):
        proc.append(f"曾浮盈约 {usd(r['mfe'])} 后转亏")
    judge = []
    if r.get("strike_reached") is not None:
        judge.append(f"行权价 {r['strike']:g} 离进场价 {pct(r['need_move'], digits=2)}，到期前标的最远到 "
                     f"{r['best_price']:g}（{pct(r['best_move'], digits=2)}），"
                     + ("碰到了行权价" if r["strike_reached"] else "没碰到行权价"))
    def verdict(right, flat):
        return "基本没动" if flat else ("方向对" if right else "方向错")
    if r.get("move_hold") is not None:
        em = f"（同期隐含波动 ±{pct(r['expected_move_hold'], False, 1)}）" if r.get("expected_move_hold") else ""
        judge.append(f"持有期间标的 {pct(r['move_hold'], digits=2)}{em}，"
                     f"{verdict(r.get('dir_right_hold'), r.get('dir_flat_hold'))}")
    if r.get("move_horizon") is not None:
        judge.append(f"到期为止标的 {pct(r['move_horizon'], digits=2)}，"
                     f"{verdict(r.get('dir_right_horizon'), r.get('dir_flat_horizon'))}")
    after = ""
    if r.get("post_move") is not None:
        pm = r["post_missed"]
        verdict = ("走得对" if pm < 0 else "卖早了" if pm > 0 else "差不多") if r["pnl"] > 0 else \
                  ("砍对了" if pm < 0 else "砍在了低点" if pm > 0 else "差不多")
        after = (f"<div class='row'><span class='k'>离场后</span>{esc(r.get('post_label', '之后'))}"
                 f"标的 {pct(r['post_move'], digits=1)}，"
                 f"继续拿着估计还会 <b class='{pn(pm)}'>{usd(pm)}</b>（{verdict}）</div>")
    tags = "".join(f"<span class='chip'>{esc(v)}</span>" for k, v in (r.get("tags") or {}).items()
                   if k not in ("标的", "结构"))
    notes = "".join(f"<div class='row muted'>注：{esc(n)}</div>" for n in r.get("notes", []))
    return (f"<div class='card trade'><h3>{title}</h3>"
            + "".join(f"<div class='row'><span class='k'>{esc(k)}</span>{esc(v)}</div>"
                      for k, v in (r.get("note") or {}).items())
            + (f"<div class='row'><span class='k'>插针</span>{esc(spike_text(r.get('spike')))}</div>"
               if r.get("spike") else "")
            + f"<div class='row'><span class='k'>盘面</span>{'；'.join(ctx)}</div>"
            f"<ul class='ev'>{ev}</ul>"
            f"<div class='row'><span class='k'>过程</span>{'；'.join(proc)}</div>"
            + (f"<div class='row'><span class='k'>判断</span>{'；'.join(judge)}</div>" if judge else "")
            + after + decomp_bar(r.get("decomp")) + f"<div>{tags}</div>{notes}</div>")


def render(res: dict, recs: list[dict], meta: dict) -> str:
    from .patterns import fmt_hold
    hd = headline(res, recs, day=bool(meta.get("day_mode")))
    ov = hd["overall"] or {}
    closed = [r for r in recs if r["status"] == "closed"]
    tot = defaultdict_sum(closed)
    parts = [f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>期权交易复盘</title><style>{CSS}</style></head><body><main>"]
    parts.append(f"<h1>期权交易复盘 · {esc(meta['range'])}</h1>"
                 f"<div class='sub'>{esc(meta['account'])} · 已了结 {ov.get('n', 0)} 笔 · "
                 f"数据截至 {esc(meta['asof'])}{' · <b>演示数据</b>' if meta.get('demo') else ''}</div>")
    # 第一屏
    hero = "".join(f"<p>{x}</p>" for x in hd["lines"][:3]) or "<p>已了结的交易太少，还看不出模式。</p>"
    if hd["next"]:
        hero += f"<p class='next'><b>下一步验证</b>：{hd['next']}</p>"
    parts.append(f"<div class='card hero'>{hero}</div>")
    if ov:
        parts.append("<div class='kpis'>"
                     f"<div class='kpi'><span>净盈亏</span><b class='{pn(ov['pnl'])}'>{usd(ov['pnl'])}</b></div>"
                     f"<div class='kpi'><span>胜率</span><b>{pct(ov['win_rate'], False)}</b></div>"
                     f"<div class='kpi'><span>平均赚 / 平均亏</span><b>{usd(ov['avg_win'], False)} / {usd(ov['avg_loss'], False)}</b></div>"
                     f"<div class='kpi'><span>方向看对（到期为止）</span><b>{pct(ov.get('dir_right_horizon'), False)}</b></div>"
                     "</div>")
    if hd.get("all") and (len(hd["all"]) > 2 or meta.get("day_mode")):
        title = "放进策略开始以来的全部交易看" if meta.get("day_mode") else "这几笔的共同点"
        parts.append(f"<h2>{title}</h2><div class='card'>"
                     + "".join(f"<p>{x}</p>" for x in hd["all"]) + "</div>")
    # 1 盈亏从哪来
    parts.append("<h2>盈亏从哪来</h2><p class='sub'>每笔按开平仓时刻的标的价格反推隐含波动率，把盈亏拆成："
                 "标的走动带来的（方向）、持有期间时间价值流逝（时间损耗）、隐含波动率涨跌和买卖价差（波动率）、手续费。</p>")
    parts.append(f"<div class='card'>{decomp_bar(tot)}</div>")
    by_dir = res["by_dim"].get("方向", [])
    if by_dir:
        parts.append("<div class='card'>" + "".join(
            f"<h3>{esc(s['value'])}：{s['n']} 笔，净 <span class='{pn(s['pnl'])}'>{usd(s['pnl'])}</span></h3>"
            + decomp_bar(s.get("decomp")) for s in by_dir) + "</div>")
    # 2 方向判断 vs 结果
    parts.append("<h2>方向看对了吗，看对为什么还亏</h2><p class='sub'>同一笔分两个口径：持有期间（开仓到离场）"
                 "与到期为止（开仓到合约到期日）。两者差得多，说明问题在时点，不在观点。</p>")
    for key, name in (("dir_right_hold", "持有期间"), ("dir_right_horizon", "到期为止")):
        q = res["quadrant"].get(key, {})
        if not q:
            continue
        cells = ""
        for k, lab in (("对赚", "方向对 · 赚"), ("对亏", "方向对 · 亏"), ("错赚", "方向错 · 赚"), ("错亏", "方向错 · 亏")):
            st = q.get(k)
            if st:
                cul = _culprit(st.get("decomp"))
                why = f"<div class='sub'>主要亏在{COMP_TXT[cul]}</div>" if cul and st["pnl"] < 0 else ""
                cells += (f"<div class='card'><div class='sub'>{lab}</div><b>{st['n']} 笔</b> "
                          f"<span class='{pn(st['pnl'])}'>{usd(st['pnl'])}</span>{why}</div>")
            else:
                cells += f"<div class='card'><div class='sub'>{lab}</div><b>0 笔</b></div>"
        parts.append(f"<h3>{name}</h3><div class='quad'>{cells}</div>")
    # 3 打法
    parts.append("<h2>哪类打法在赚，哪类在漏</h2><p class='sub'>按进场盘面、期限、虚实、贵贱、加仓与离场方式等组合分组，"
                 "挑出净盈亏最大的几类（彼此重叠过半的只留一个）。少于 3 笔的组不下结论。</p>")
    for title, items in (("在赚的打法", res["edges"]), ("在漏的打法", res["leaks"])):
        if not items:
            continue
        parts.append(f"<h3>{title}</h3>")
        for st in items:
            parts.append(f"<div class='card pat'><b>{esc(st['label'])}</b> · {st['n']} 笔 · 胜率 "
                         f"{pct(st['win_rate'], False)} · 净 <b class='{pn(st['pnl'])}'>{usd(st['pnl'])}</b>"
                         f"<div class='why'>{esc(st['why']) or '—'}</div>{decomp_bar(st.get('decomp'))}</div>")
    parts.append("<h3>按单个维度看</h3>")
    for dim, rows in res["by_dim"].items():
        if not rows:
            continue
        chart = bars([(r["value"], r["pnl"], f"{r['value']}：{r['n']} 笔，胜率 {pct(r['win_rate'], False)}，净 {usd(r['pnl'])}")
                      for r in rows if r["enough"]])
        table = "<div class='tw'><table>" + TABLE_HEAD + "".join(_stat_row(r) for r in rows) + "</table></div>"
        parts.append(f"<details><summary>{esc(dim)}（{len(rows)} 组）</summary>{chart}{table}</details>")
    # 4 进出场与行为
    ex, beh = res["exit"], res["behavior"]
    lines = []
    if ex.get("hold_win") is not None and ex.get("hold_loss") is not None:
        from .patterns import fmt_hold
        lines.append(f"赚钱的单中位持有 {fmt_hold(ex['hold_win'])}，亏钱的 {fmt_hold(ex['hold_loss'])}。")
    if ex.get("capture_win") is not None:
        lines.append(f"赚钱的单中位只拿到持仓期间最大浮盈的 {pct(ex['capture_win'], False)}。")
    def _post_list(rs):
        rs = sorted([r for r in rs if r.get("post_missed") is not None], key=lambda r: -abs(r["post_missed"]))
        return "；".join(f"{esc(r.get('main_leg') or r['underlying'])} {usd(r['post_missed'])}"
                        f"（{esc(r.get('post_label', ''))}标的 {pct(r['post_move'], digits=1)}）" for r in rs[:4])
    wins_c = [r for r in closed if r["pnl"] > 0]
    loss_c = [r for r in closed if r["pnl"] <= 0]
    if ex.get("missed_after_win") is not None:
        lines.append(f"离场后看到合约到期（当天到期的看到当天收盘，还没到期的看到现在）。赚钱的单继续拿着估计合计 "
                     f"{usd(ex['missed_after_win'])}：{_post_list(wins_c)}。")
    if ex.get("missed_after_loss") is not None:
        lines.append(f"亏钱的单继续拿着估计合计 {usd(ex['missed_after_loss'])}（负数=砍对了，正数=砍在低点）："
                     f"{_post_list(loss_c)}。")
    if ex.get("expired_n"):
        lines.append(f"拿到到期作废 {ex['expired_n']} 笔，合计 {usd(ex['expired_pnl'])}。")
    if beh.get("top3_share") is not None:
        lines.append(f"最赚的 3 笔贡献了总盈利的 {pct(beh['top3_share'], False)}。")
    lines.append(f"最长连亏 {beh.get('max_loss_streak', 0)} 笔。")
    parts.append("<h2>进出场习惯</h2><div class='card'>" + "".join(f"<p>{x}</p>" for x in lines) + "</div>")
    for key, name in (("前一笔", "上一笔结果之后"), ("仓位", "仓位大小")):
        rows = [dict(v, value=k) for k, v in beh.get(key, {}).items()]
        if rows:
            parts.append(f"<h3>{name}</h3><div class='tw'><table>{TABLE_HEAD}"
                         + "".join(_stat_row(r) for r in sorted(rows, key=lambda x: -x['pnl'])) + "</table></div>")
    # 5 逐笔
    picks = sorted(closed, key=lambda r: -abs(r["pnl"]))[:8]
    odd = [r for r in closed if r not in picks and r.get("dir_right_hold") is not None
           and (r["dir_right_hold"] != (r["pnl"] > 0))]
    picks += sorted(odd, key=lambda r: -abs(r["pnl"]))[:4]
    parts.append("<h2>逐笔看逻辑</h2><p class='sub'>盈亏最大的几笔，加上「方向对却亏」「方向错却赚」中金额最大的几笔。</p>")
    parts.extend(_trade_card(r) for r in picks)
    rest = sorted([r for r in recs if r not in picks], key=lambda r: r["open_ts"])
    if rest:
        rows = "".join(
            f"<tr><td>{datetime.fromisoformat(r['open_ts']):%m/%d} {esc(r['underlying'])} {esc(r.get('main_leg', ''))}</td>"
            f"<td>{esc(r.get('structure'))}</td><td>{esc(r['status'])}</td><td class='{pn(r['pnl'])}'>{usd(r['pnl'])}</td>"
            f"<td>{pct(r.get('ret'))}</td><td>{fmt_hold(r['hold_days'])}</td></tr>" for r in rest)
        parts.append(f"<details><summary>其余 {len(rest)} 笔</summary><div class='tw'><table>"
                     f"<tr><th>交易</th><th>结构</th><th>状态</th><th>盈亏</th><th>收益率</th><th>持有</th></tr>"
                     f"{rows}</table></div></details>")
    # 6 数据说明
    notes = "".join(f"<li>{esc(x)}</li>" for x in meta.get("notes", []))
    parts.append(f"<h2>数据说明</h2><ul class='sub'>{notes}</ul></main></body></html>")
    return "".join(parts)


def defaultdict_sum(recs: list[dict]) -> dict | None:
    out: dict[str, float] = {}
    for r in recs:
        for k, v in (r.get("decomp") or {}).items():
            out[k] = out.get(k, 0.0) + v
    return out or None
