"""路径、时区与用户可调参数。

凭据只从本机目录读取，不写进仓库或报告：
- 默认目录 ~/.config/tiger/，放老虎开发者页面导出的 tiger_openapi_config.properties
- 可用环境变量 TIGER_PROPS_DIR 覆盖
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
PROPS_DIR = Path(os.environ.get("TIGER_PROPS_DIR", Path.home() / ".config" / "tiger"))
SETTINGS_PATH = DATA_DIR / "settings.json"

# ---- 美股交易日历（休市日 + 13:00 半日市）----
# 正本在团队仓库 info-heat-card/references/us_market_holidays.json；读不到就用下面这份内置的兜底。
CALENDAR_FILE = Path.home() / "Codes" / "ai-trading-team" / ".claude" / "skills" / "info-heat-card" / "references" / "us_market_holidays.json"
_FALLBACK_FULL = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
                  "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
_FALLBACK_EARLY = {"2026-11-27": "13:00", "2026-12-24": "13:00", "2027-11-26": "13:00"}


@lru_cache(maxsize=1)
def _calendar() -> tuple[frozenset, dict]:
    try:
        doc = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
        return frozenset(doc.get("full_close") or []), dict(doc.get("early_close") or {})
    except (OSError, ValueError):
        return frozenset(_FALLBACK_FULL), dict(_FALLBACK_EARLY)


def is_trading_day(d: date) -> bool:
    """美股常规时段当天有交易：工作日且不在休市名单里。"""
    return d.weekday() < 5 and d.isoformat() not in _calendar()[0]


def prev_trading_day(d: date) -> date:
    """严格早于 d 的最近一个交易日。"""
    n = d - timedelta(days=1)
    while not is_trading_day(n):
        n -= timedelta(days=1)
    return n


def close_hm(d: date) -> str:
    """当天常规时段收盘时刻（美东 HH:MM）：半日市 13:00，其余 16:00。"""
    return _calendar()[1].get(d.isoformat(), "16:00")


def _early(d: date) -> bool:
    return close_hm(d) == "13:00"


def grab_hm(d: date) -> str:
    """收盘前存期权和标的分钟线的起点：全天市 15:54，半日市 12:54。"""
    return "12:54" if _early(d) else "15:54"


def last_bar_hm(d: date) -> str:
    """算「分钟线走到收盘」的最后一根：全天市 15:59，半日市 12:59。"""
    return "12:59" if _early(d) else "15:59"


def default_flat_by(d: date) -> str:
    """卡上没写了结时刻时的默认值：全天市 15:30，半日市 12:30。"""
    return "12:30" if _early(d) else "15:30"


@dataclass
class Settings:
    """分析参数。这里只放「怎么度量」的参数，不放任何纪律阈值。

    复盘目标是还原交易逻辑与模式（方向判断、盈亏来源、进场盘面、打法胜负），
    不是拿规则逐笔判违规。
    """

    # 同一标的两笔订单间隔多少分钟内，视为同一个组合（手动分腿下单 / 移仓）
    leg_window_minutes: int = 15
    # 无风险利率，仅用于从成交价反推隐含波动率做盈亏拆解
    risk_free_rate: float = 0.045
    # 平仓后观察多少个交易日，看行情是否继续朝原方向走（判断卖早 / 卖对）
    post_exit_days: int = 5
    # 一个打法至少多少笔才下结论，少于此数只列为「样本不足」
    min_pattern_n: int = 3
    account_label: str = "老虎期权账户"
    # 当前期权策略的开始日：复盘只算这天以后的期权交易（之前的正股等交易不属于该策略）
    strategy_start: str = "2026-10-16"
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        """读取 data/settings.json，不存在则用默认值。"""
        if SETTINGS_PATH.exists():
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            return cls(**known)
        return cls()

    def save(self) -> None:
        """写回 data/settings.json。"""
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                                 encoding="utf-8")
