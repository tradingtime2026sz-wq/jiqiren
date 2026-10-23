"""路径、时区与用户可调参数。

凭据只从本机目录读取，不写进仓库或报告：
- 默认目录 ~/.config/tiger/，放老虎开发者页面导出的 tiger_openapi_config.properties
- 可用环境变量 TIGER_PROPS_DIR 覆盖
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
PROPS_DIR = Path(os.environ.get("TIGER_PROPS_DIR", Path.home() / ".config" / "tiger"))
SETTINGS_PATH = DATA_DIR / "settings.json"


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
