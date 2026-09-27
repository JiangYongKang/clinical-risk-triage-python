"""领域模型与枚举。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CaseStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    TRIAGED = "TRIAGED"
    REVIEWING = "REVIEWING"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


@dataclass(frozen=True)
class RiskFactor:
    code: str
    label: str
    weight: int


@dataclass(frozen=True)
class RuleSet:
    """不可变的规则版本定义：发布后不允许修改，保证历史结论可复现。"""
    version: str
    description: str
    thresholds: tuple[int, int, int]  # (MEDIUM, HIGH, CRITICAL) 分数线
    factors: tuple[RiskFactor, ...]


@dataclass(frozen=True)
class RiskAssessment:
    """一次评估结论，绑定其使用的规则版本。"""
    version: str
    level: RiskLevel
    score: int
    matched: tuple[RiskFactor, ...]
    reasons: tuple[str, ...]
    computed_at: str

    def to_dict(self) -> dict:
        return {
            "rule_version": self.version,
            "level": self.level.value,
            "score": self.score,
            "matched_factors": [
                {"code": f.code, "label": f.label, "weight": f.weight}
                for f in self.matched
            ],
            "reasons": list(self.reasons),
            "computed_at": self.computed_at,
        }


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    timestamp: str
    action: str
    detail: str
    rule_version: str | None = None

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "action": self.action,
            "detail": self.detail,
            "rule_version": self.rule_version,
        }


@dataclass
class CaseRecord:
    case_id: str
    idempotency_key: str
    patient: dict
    status: CaseStatus
    created_at: str
    updated_at: str
    assessment: RiskAssessment | None = None
    history: list[RiskAssessment] = field(default_factory=list)
    audit: list[AuditEvent] = field(default_factory=list)
    audit_seq: int = 0

    def snapshot(self) -> dict:
        """对外可追溯的只读快照。"""
        return {
            "case_id": self.case_id,
            "idempotency_key": self.idempotency_key,
            "patient": dict(self.patient),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "assessment": self.assessment.to_dict() if self.assessment else None,
            "history": [a.to_dict() for a in self.history],
            "audit": [e.to_dict() for e in self.audit],
        }


def utcnow_iso() -> str:
    """统一 UTC ISO8601 时间戳。"""
    return datetime.now(timezone.utc).isoformat()
