"""核心数据模型：病例、评估结论、规则版本与状态枚举。"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


class CaseStatus(str, enum.Enum):
    """病例状态机。"""

    SUBMITTED = "SUBMITTED"
    EVALUATED = "EVALUATED"
    REVIEWED = "REVIEWED"
    CLOSED = "CLOSED"


class RiskLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# 合法状态流转表：非法跃迁必须被拒绝
ALLOWED_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.SUBMITTED: frozenset({CaseStatus.EVALUATED}),
    CaseStatus.EVALUATED: frozenset({CaseStatus.REVIEWED}),
    CaseStatus.REVIEWED: frozenset({CaseStatus.CLOSED}),
    CaseStatus.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class PatientCase:
    """患者病例输入。"""

    case_id: str
    age: int
    systolic_bp: int
    heart_rate: int
    temperature_c: float
    spo2: float
    symptoms: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleVersion:
    """不可变的决策规则版本。"""

    version: str
    thresholds: dict[str, float]
    symptom_weights: dict[str, float]
    level_cutoffs: dict[str, float]
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True)
class Assessment:
    """一次评估结论，绑定规则版本，含可解释的判定依据。"""

    case_id: str
    rule_version: str
    risk_level: RiskLevel
    score: float
    rationale: tuple[str, ...]
    evaluated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
