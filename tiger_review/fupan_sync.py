"""复盘同步到私有仓库 jiqiren-fupan（复盘报告含个人盈亏，不进公开的 jiqiren）。

同步的东西：
- reports/ 下全部复盘与研究报告（HTML / JSON）
- data/notes.json（选票前提）、data/raw/auto_state.json（已复盘到哪一笔，防重报）、data/settings.json
换机器时先 pull：备注按键合并，复盘状态取「已报告到更晚那一笔」的版本。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import DATA_DIR, REPORTS_DIR

PRIV = Path(os.environ.get("JIQIREN_FUPAN_DIR", Path.home() / "Codes" / "jiqiren-fupan"))
STATE_FILES = ("notes.json", "raw/auto_state.json", "settings.json")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(PRIV), *args], capture_output=True, text=True, timeout=120)


def ready() -> bool:
    return (PRIV / ".git").exists()


def merge_state(local: dict | None, remote: dict | None) -> dict | None:
    """两份「已复盘到哪」取更晚的那份，避免重报或漏报。"""
    if not local:
        return remote
    if not remote:
        return local
    return local if int(local.get("last_fill_ms", 0)) >= int(remote.get("last_fill_ms", 0)) else remote


def merge_notes(local: dict | None, remote: dict | None) -> dict:
    """选票备注按键合并：两边都有的键以本机为准。"""
    out = dict(remote or {})
    out.update(local or {})
    return out


def push(message: str) -> str:
    """把报告和状态拷进私有仓库并推送。返回一句结果说明，失败不抛异常。"""
    if not ready():
        return f"没找到私有复盘仓库 {PRIV}，跳过上传"
    _git("pull", "-q", "--rebase", "origin", "main")
    dst_reports = PRIV / "reports"
    dst_reports.mkdir(parents=True, exist_ok=True)
    for f in REPORTS_DIR.glob("*"):
        if f.is_file() and f.suffix in (".html", ".json"):
            shutil.copy2(f, dst_reports / f.name)
    for rel in STATE_FILES:
        src = DATA_DIR / rel
        if src.exists():
            (PRIV / "data" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, PRIV / "data" / rel)
    _git("add", "-A")
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return "私有复盘仓库没有变化"
    c = _git("commit", "-q", "-m", message)
    if c.returncode != 0:
        return "私有复盘仓库提交失败"
    p = _git("push", "-q", "origin", "main")
    return "已上传到私有复盘仓库" if p.returncode == 0 else "已在本机提交，推送失败，下次再推"


def pull() -> str:
    """换机器时用：从私有仓库取回备注和复盘状态，合并进本机 data/。"""
    if not ready():
        return f"没找到私有复盘仓库 {PRIV}"
    _git("pull", "-q", "--rebase", "origin", "main")
    for rel in STATE_FILES:
        remote_f, local_f = PRIV / "data" / rel, DATA_DIR / rel
        if not remote_f.exists():
            continue
        remote = json.loads(remote_f.read_text(encoding="utf-8"))
        local = json.loads(local_f.read_text(encoding="utf-8")) if local_f.exists() else None
        if rel.endswith("auto_state.json"):
            merged = merge_state(local, remote)
        elif rel == "notes.json":
            merged = merge_notes(local, remote)
        else:
            merged = local or remote
        local_f.parent.mkdir(parents=True, exist_ok=True)
        local_f.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for f in (PRIV / "reports").glob("*"):
        if f.is_file() and not (REPORTS_DIR / f.name).exists():
            shutil.copy2(f, REPORTS_DIR / f.name)
    return "已从私有复盘仓库取回备注、复盘状态和历史报告"
