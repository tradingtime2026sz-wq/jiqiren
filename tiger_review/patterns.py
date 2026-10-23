"""从逐笔记录里找模式：哪类打法在赚、哪类在漏，以及背后的机制。

所有结论都来自你自己的成交数据；样本少于 min_pattern_n 的组只列不下结论。
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import combinations

DIRECTIONAL = ("看涨", "看跌", "中性偏空")


def _dir_sign(bias: str) -> int:
    return 1 if bias == "看涨" else -1


def label(rec: dict) -> dict:
    """给一笔打上可解释的标签。"""
    bias = rec.get("bias") or "混合"
    tags = {"结构": rec.get("structure") or "未知", "方向": bias,
            "标的": rec["underlying"], "时段": rec.get("entry_bucket", "未知")}
    if bias in DIRECTIONAL:
        z = rec.get("pre5_z")
        if z is not None:
            zd = z * _dir_sign(bias)
            tags["进场盘面"] = ("顺势追：前5天已朝你的方向大走" if zd > 1
                            else "逆势抄：前5天朝反方向大走" if zd < -1 else "平稳中进场")
        rp = rec.get("range_pos")
        if rp is not None:
            rd = rp if _dir_sign(bias) > 0 else 1 - rp
            tags["日内位置"] = ("日内追：进在当天顺向极端" if rd >= 0.75
                            else "日内接：进在当天逆向极端" if rd <= 0.25 else "日内中段")
    dte = rec.get("dte_open")
    if dte is not None:
        tags["期限"] = ("0DTE 当天到期" if dte == 0 else "1周内到期" if dte <= 7
                      else "1个月内到期" if dte <= 31 else "1个月以上")
    dm = rec.get("delta_main")
    if dm is not None:
        a = abs(dm)
        tags["虚实"] = ("深度虚值(Δ<0.25)" if a < 0.25 else "虚值(Δ0.25-0.45)" if a < 0.45
                      else "平值(Δ0.45-0.6)" if a <= 0.6 else "实值(Δ>0.6)")
    ih = rec.get("iv_hv")
    if ih is not None:
        tags["期权贵贱"] = ("便宜：隐波低于实际波动" if ih < 0.9 else "偏贵：隐波高出实际波动25%以上"
                        if ih > 1.25 else "隐波≈实际波动")
    sp = rec.get("spike")
    if sp:
        tags["回落深度"] = sp["depth"]
        tags["插针打分"] = f"{sp['score']} 分"
    adds = rec.get("adds")
    if adds is not None:
        tags["加仓"] = adds
    ex = rec.get("exit_style")
    if ex:
        tags["离场方式"] = ex
    ea = rec.get("exit_adverse")
    if ea is not None:
        tags["离场位置"] = ("走在标的最不利处(前后1小时)" if ea >= 0.85
                        else "走在标的最有利处(前后1小时)" if ea <= 0.15 else "走在中段")
    hd = rec.get("hold_days")
    if hd is not None:
        tags["持有"] = ("日内" if hd < 0.8 else "1-3天" if hd <= 3 else "4-10天" if hd <= 10
                      else "10天以上")
    return tags


def add_sequence_tags(recs: list[dict]) -> None:
    """前一笔结果、仓位大小这类行为标签，需要按时间顺序看。"""
    done = sorted([r for r in recs if r.get("close_ts")], key=lambda r: r["close_ts"])
    sizes = [abs(r["open_debit"]) for r in recs if r.get("open_debit")]
    med = statistics.median(sizes) if sizes else 0
    for r in recs:
        t = r["open_ts"]
        prev = [x for x in done if x["close_ts"] <= t and x["cid"] != r["cid"]]
        if not prev:
            r["tags"]["前一笔"] = "首笔"
        else:
            last = prev[-1]
            gap = datetime.fromisoformat(t) - datetime.fromisoformat(last["close_ts"])
            if last["pnl"] < 0:
                r["tags"]["前一笔"] = "亏完2小时内再开" if gap <= timedelta(hours=2) else "亏后"
            else:
                r["tags"]["前一笔"] = "赚后"
        same = [x for x in prev if x["underlying"] == r["underlying"]]
        if same and same[-1]["pnl"] < 0 and same[-1].get("open_debit"):
            r["size_vs_prev"] = abs(r.get("open_debit") or 0) / abs(same[-1]["open_debit"])
            r["prev_same_cid"] = same[-1]["cid"]
            if r["size_vs_prev"] > 1.5:
                r["tags"]["亏后仓位"] = "同标的亏后放大仓位"
        if med:
            ratio = abs(r.get("open_debit") or 0) / med
            r["tags"]["仓位"] = "偏大(>1.5倍常规)" if ratio > 1.5 else "偏小" if ratio < 0.67 else "常规"


def _stats(group: list[dict]) -> dict:
    n = len(group)
    pnl = sum(r["pnl"] for r in group)
    wins = [r for r in group if r["pnl"] > 0]
    comp = defaultdict(float)
    nd = 0
    for r in group:
        if r.get("decomp"):
            nd += 1
            for k, v in r["decomp"].items():
                comp[k] += v
    def rate(key):
        vals = [r[key] for r in group if r.get(key) is not None]
        return (sum(1 for v in vals if v) / len(vals)) if vals else None
    ivhv = [r["iv_hv"] for r in group if r.get("iv_hv")]
    caps = [r["capture"] for r in wins if r.get("capture") is not None]
    ext = max(group, key=lambda r: abs(r["pnl"])) if group else None
    trimmed = None
    share = None
    if ext is not None and n >= 2:
        same = [r for r in group if (r["pnl"] > 0) == (pnl > 0)]
        top = max(same, key=lambda r: abs(r["pnl"])) if same else ext
        trimmed = pnl - top["pnl"]
        share = abs(top["pnl"]) / abs(pnl) if pnl else None
    return {
        "trimmed": round(trimmed, 2) if trimmed is not None else None, "one_trade_share": share,
        "n": n, "wins": len(wins), "win_rate": len(wins) / n if n else 0, "pnl": round(pnl, 2),
        "avg": round(pnl / n, 2) if n else 0,
        "avg_win": round(statistics.mean(r["pnl"] for r in wins), 2) if wins else 0,
        "avg_loss": round(statistics.mean(r["pnl"] for r in group if r["pnl"] <= 0), 2)
        if n > len(wins) else 0,
        "decomp": {k: round(v, 2) for k, v in comp.items()} if nd else None,
        "dir_right_hold": rate("dir_right_hold"), "dir_right_horizon": rate("dir_right_horizon"),
        "iv_hv": statistics.mean(ivhv) if ivhv else None,
        "capture_win": statistics.median(caps) if caps else None,
        "hold_win": statistics.median([r["hold_days"] for r in wins]) if wins else None,
        "hold_loss": statistics.median([r["hold_days"] for r in group if r["pnl"] <= 0])
        if n > len(wins) else None,
        "cids": [r["cid"] for r in group],
    }


def diagnose(st: dict) -> list[tuple[str, str]]:
    """用拆解数字说明这组交易为什么赚 / 为什么亏。返回 [(诊断代码, 说明)]，第一条最重要。"""
    d = st.get("decomp")
    out: list[tuple[str, str]] = []
    if d:
        dirn, tm, vol = d.get("direction", 0), d.get("time", 0), d.get("vol", 0)
        gross = abs(dirn) + abs(tm) + abs(vol) or 1
        hz, hd = st.get("dir_right_horizon"), st.get("dir_right_hold")
        if dirn > 0 and dirn + tm + vol < 0:
            code = "eaten_by_vol" if vol < tm else "eaten_by_time"
            out.append((code, "方向其实看对了，但" + ("隐波回落和买卖价差" if code == "eaten_by_vol"
                                                  else "时间损耗") + "把方向赚的全部吃掉"))
        elif dirn < 0 and abs(dirn) / gross < 0.3 and tm + vol < 0:
            code = "eaten_by_vol" if vol < tm else "eaten_by_time"
            out.append((code, "方向本身影响不大，亏损主要来自" +
                        ("隐波回落和买卖价差" if code == "eaten_by_vol" else "时间损耗")))
        elif dirn < 0 and hz is not None and hd is not None and hz >= 0.5 and hz - hd >= 0.15:
            out.append(("timing", f"离场时方向对的只有 {hd:.0%}，拉长到到期日对的有 {hz:.0%}："
                                  "观点多半没错，错在进场时点或拿不住"))
        elif dirn < 0 and abs(dirn) / gross > 0.5:
            out.append(("thesis", "主要亏在方向：观点本身没兑现" +
                        (f"（到期为止也只对 {hz:.0%}）" if hz is not None else "")))
        elif dirn > 0 and abs(dirn) / gross > 0.5:
            out.append(("direction_edge", "钱主要来自方向判断"))
        if tm > 0 and abs(tm) / gross > 0.4:
            out.append(("theta_edge", "收的时间价值是主要利润来源"))
        if vol < 0 and abs(vol) / gross > 0.3 and not any(c == "eaten_by_vol" for c, _ in out):
            iv = st.get("iv_hv")
            out.append(("vol_hit", "买在隐波偏高处、事后回落" if iv and iv > 1.25
                        else "隐波回落和买卖价差吃掉一大块"))
        if vol > 0 and abs(vol) / gross > 0.3:
            out.append(("vol_help", "隐波上升帮了忙"))
    cw = st.get("capture_win")
    if cw is not None and cw < 0.5 and st["wins"] >= 2:
        out.append(("early_exit", f"赚钱的笔只拿到持仓期间最大浮盈的 {cw:.0%}"))
    hw, hl = st.get("hold_win"), st.get("hold_loss")
    if hw is not None and hl is not None and hl > 1.8 * max(hw, 0.05) and st["n"] - st["wins"] >= 2:
        out.append(("hold_losers", f"亏钱的笔中位拿 {fmt_hold(hl)}，赚钱的只拿 {fmt_hold(hw)}：赚了快走、亏了拖着"))
    if st.get("one_trade_share") is not None and st["one_trade_share"] > 0.6:
        out.append(("concentrated", f"其中一笔占了这组盈亏的 {st['one_trade_share']:.0%}"))
    return out


def fmt_hold(days: float | None) -> str:
    """持有时长：不到一天按分钟说，末日期权按天说没有意义。"""
    if days is None:
        return "—"
    return f"{days * 1440:.0f} 分钟" if days < 1 else f"{days:.1f} 天"


def explain(st: dict) -> str:
    return "；".join(t for _, t in diagnose(st))


DIMENSIONS = ["方向", "结构", "回落深度", "插针打分", "进场盘面", "日内位置", "期限", "虚实", "期权贵贱", "时段", "持有",
              "加仓", "离场方式", "离场位置", "前一笔", "亏后仓位", "仓位", "标的"]
PAIRS = [("方向", "进场盘面"), ("方向", "期限"), ("期限", "虚实"), ("结构", "期权贵贱"),
         ("方向", "日内位置"), ("结构", "期限"), ("前一笔", "仓位"), ("标的", "结构"),
         ("进场盘面", "期权贵贱"), ("进场盘面", "期限"), ("加仓", "结构"), ("离场方式", "结构"),
         ("时段", "期限")]


def mine(recs: list[dict], min_n: int = 3) -> dict:
    closed = [r for r in recs if r["status"] == "closed"]
    cap = max(int(len(closed) * 0.5), min_n)
    pair_n = max(min_n + 1, 4)
    for r in recs:
        r["tags"] = label(r)
    add_sequence_tags(recs)
    by_dim: dict[str, list] = {}
    cands = []
    for dim in DIMENSIONS:
        groups = defaultdict(list)
        for r in closed:
            if dim in r["tags"]:
                groups[r["tags"][dim]].append(r)
        rows = []
        for val, g in groups.items():
            st = _stats(g)
            st.update({"dim": dim, "value": val, "label": f"{dim}：{val}",
                       "enough": len(g) >= min_n})
            st["why"] = explain(st)
            st["diag"] = [c for c, _ in diagnose(st)]
            rows.append(st)
            if min_n <= len(g) <= cap and dim not in ("标的",):
                cands.append(st)
        by_dim[dim] = sorted(rows, key=lambda x: x["pnl"], reverse=True)
    for a, b in PAIRS:
        groups = defaultdict(list)
        for r in closed:
            if a in r["tags"] and b in r["tags"]:
                groups[(r["tags"][a], r["tags"][b])].append(r)
        for (va, vb), g in groups.items():
            if not pair_n <= len(g) <= cap:
                continue
            st = _stats(g)
            st.update({"dim": f"{a}×{b}", "value": f"{va} × {vb}", "label": f"{va} + {vb}",
                       "enough": True})
            st["why"] = explain(st)
            st["diag"] = [c for c, _ in diagnose(st)]
            cands.append(st)
    # 去掉组内最极端的一笔后仍同号，才算模式（防一笔大单撑起一个「打法」）
    edges = _pick([c for c in cands if c["pnl"] > 0 and (c["trimmed"] or 0) > 0], reverse=True)
    leaks = _pick([c for c in cands if c["pnl"] < 0 and (c["trimmed"] or 0) < 0], reverse=False)
    return {"by_dim": by_dim, "edges": edges, "leaks": leaks,
            "quadrant": quadrant(closed), "exit": exit_behavior(closed),
            "behavior": behavior(closed), "overall": _stats(closed) if closed else None}


def _pick(cands: list[dict], reverse: bool, k: int = 3) -> list[dict]:
    """按净盈亏挑前 k 个，彼此重叠超过一半的只留更具体 / 更大的那个。"""
    cands = sorted(cands, key=lambda c: (c["trimmed"], "×" in c["dim"]), reverse=reverse)
    out: list[dict] = []
    for c in cands:
        s = set(c["cids"])
        if any(len(s & set(o["cids"])) / len(s | set(o["cids"])) > 0.5 for o in out):
            continue
        out.append(c)
        if len(out) == k:
            break
    return out


def quadrant(closed: list[dict]) -> dict:
    """方向对 / 错 × 赚 / 亏 四格。方向按「持有期间」与「到期为止」两个口径。"""
    out = {}
    for key in ("dir_right_hold", "dir_right_horizon"):
        cells = defaultdict(list)
        for r in closed:
            v = r.get(key)
            if v is None:
                continue
            cells[("对" if v else "错") + ("赚" if r["pnl"] > 0 else "亏")].append(r)
        out[key] = {k: _stats(v) for k, v in cells.items()}
    return out


def exit_behavior(closed: list[dict]) -> dict:
    wins = [r for r in closed if r["pnl"] > 0]
    losses = [r for r in closed if r["pnl"] <= 0]
    def med(rs, k):
        v = [r[k] for r in rs if r.get(k) is not None]
        return statistics.median(v) if v else None
    def tot(rs, k):
        v = [r[k] for r in rs if r.get(k) is not None]
        return round(sum(v), 2) if v else None
    return {
        "hold_win": med(wins, "hold_days"), "hold_loss": med(losses, "hold_days"),
        "capture_win": med(wins, "capture"),
        "mae_win": med(wins, "mae"),
        "missed_after_win": tot(wins, "post_missed"), "missed_after_loss": tot(losses, "post_missed"),
        "n_win_post": sum(1 for r in wins if r.get("post_missed") is not None),
        "n_loss_post": sum(1 for r in losses if r.get("post_missed") is not None),
        "post_move_win": med(wins, "post_move"),
        "expired_n": sum(1 for r in closed if "expired" in r.get("exit_kinds", [])),
        "expired_pnl": round(sum(r["pnl"] for r in closed if "expired" in r.get("exit_kinds", [])), 2),
    }


def behavior(closed: list[dict]) -> dict:
    out = {}
    for key in ("前一笔", "仓位"):
        groups = defaultdict(list)
        for r in closed:
            if key in r.get("tags", {}):
                groups[r["tags"][key]].append(r)
        out[key] = {k: _stats(v) for k, v in groups.items()}
    pnls = sorted((r["pnl"] for r in closed), reverse=True)
    total = sum(pnls)
    out["top3_share"] = (sum(pnls[:3]) / total) if total > 0 and len(pnls) > 3 else None
    streak = best = 0
    for r in sorted(closed, key=lambda r: r["close_ts"]):
        streak = streak + 1 if r["pnl"] <= 0 else 0
        best = max(best, streak)
    out["max_loss_streak"] = best
    return out
