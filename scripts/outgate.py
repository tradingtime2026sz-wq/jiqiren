#!/usr/bin/env python3
"""发出前的两道检查的接线（river 2026-11-10：打回要变成红灯，不过就不发）。

规则表和断言表在团队仓库 ai-trading-team/automation/（outgoing-rules.json、content-checks.json），
这里只负责找到它们并调用。团队仓库不在这台机器上时放行并在 stderr 说一声：检查坏了不能把消息全拦住。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

TEAM_SCRIPTS = Path(os.environ.get("TEAM_REPO", Path.home() / "Codes" / "ai-trading-team")) / "automation" / "scripts"


def _load(name: str):
    """Import a checker module from the team repo; None when it is not there."""
    if not (TEAM_SCRIPTS / f"{name}.py").exists():
        print(f"[outgate] 找不到 {TEAM_SCRIPTS / (name + '.py')}，这次没查", file=sys.stderr)
        return None
    if str(TEAM_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(TEAM_SCRIPTS))
    try:
        return __import__(name)
    except Exception as exc:  # 检查脚本自己坏了：放行，但留痕
        print(f"[outgate] {name} 导入失败（{type(exc).__name__}: {exc}），这次没查", file=sys.stderr)
        return None


def lint(text: str, profile: str, source: str = "", enforce: bool = True) -> Tuple[bool, str]:
    """Run the outgoing rules. Returns (no red light, report text; "" when clean).

    enforce=False：代码拼的固定模板，照查照记账，调用方不拦。
    """
    mod = _load("outgoing_check")
    if mod is None:
        return True, ""
    try:
        res = mod.gate(text, profile, source=source, enforce=enforce)
    except Exception as exc:
        print(f"[outgate] 发出前检查没跑成（{type(exc).__name__}: {exc}），放行", file=sys.stderr)
        return True, ""
    return res["ok"], (res["report"] if res["found"] else "")


def content(gear: str, day: str, only: Optional[str] = None) -> Tuple[bool, str]:
    """Run the gear's content assertions. Returns (no red light, one-line reason)."""
    mod = _load("content_check")
    if mod is None:
        return True, ""
    return mod.gate(gear, date.fromisoformat(day), only=only)
