"""决策规则的多版本定义。

规则版本一经发布即不可变（frozen dataclass + 模块级注册表），
历史评估结论绑定其规则版本，更新规则不会改写历史结论。
"""
from __future__ import annotations

from app.errors import ErrorCode, TriageError
from app.models import RiskFactor, RuleSet

_RULE_V1 = RuleSet(
    version="v1.0",
    description="初始版分诊规则",
    thresholds=(3, 6, 9),  # MEDIUM / HIGH / CRITICAL
    factors=(
        RiskFactor("AGE_GE_65", "年龄>=65", 2),
        RiskFactor("HR_TACHY", "心率>110", 2),
        RiskFactor("SBP_LOW", "收缩压<90", 3),
        RiskFactor("SPO2_LOW", "血氧饱和度<92", 3),
        RiskFactor("TEMP_HIGH", "体温>=39", 2),
        RiskFactor("COMORBID", "存在合并症", 2),
        RiskFactor("CONSCIOUSNESS", "意识障碍", 4),
    ),
)

_RULE_V2 = RuleSet(
    version="v2.0",
    description="强化版：上调低血氧权重并收紧分级阈值",
    thresholds=(2, 5, 8),  # MEDIUM / HIGH / CRITICAL
    factors=(
        RiskFactor("AGE_GE_65", "年龄>=65", 2),
        RiskFactor("AGE_GE_80", "年龄>=80", 3),
        RiskFactor("HR_TACHY", "心率>110", 2),
        RiskFactor("SBP_LOW", "收缩压<90", 3),
        RiskFactor("SPO2_LOW", "血氧饱和度<92", 4),
        RiskFactor("TEMP_HIGH", "体温>=39", 2),
        RiskFactor("COMORBID", "存在合并症", 2),
        RiskFactor("CONSCIOUSNESS", "意识障碍", 4),
    ),
)

# 已发布的规则版本注册表（只增不改）。
RULE_VERSIONS: dict[str, RuleSet] = {
    _RULE_V1.version: _RULE_V1,
    _RULE_V2.version: _RULE_V2,
}

LATEST_VERSION: str = _RULE_V2.version


def get_rule(version: str) -> RuleSet:
    """按版本获取规则，不存在则抛 RULE_VERSION_NOT_FOUND。"""
    rule = RULE_VERSIONS.get(version)
    if rule is None:
        raise TriageError(
            ErrorCode.RULE_VERSION_NOT_FOUND,
            f"规则版本不存在: {version!r}，可用版本: {sorted(RULE_VERSIONS)}",
        )
    return rule


def list_versions() -> list[str]:
    """列出所有已发布规则版本。"""
    return sorted(RULE_VERSIONS)
