"""领域服务：病例提交、风险评估、状态流转、版本重算与审核日志。"""
from __future__ import annotations

import numbers
import uuid
from collections.abc import Callable

from app.engine import RuleEngine
from app.errors import ErrorCode, TriageError, normalize_engine_error
from app.models import CaseRecord, CaseStatus, utcnow_iso
from app.repository import CaseRepository
from app.rules import LATEST_VERSION, get_rule

# 状态流转表：动作 -> (允许的源状态集合, 目标状态)
ALLOWED_TRANSITIONS: dict[str, tuple[frozenset[CaseStatus], CaseStatus]] = {
    "start_review": (frozenset({CaseStatus.TRIAGED}), CaseStatus.REVIEWING),
    "confirm": (frozenset({CaseStatus.REVIEWING}), CaseStatus.CONFIRMED),
    "dismiss": (frozenset({CaseStatus.REVIEWING}), CaseStatus.DISMISSED),
    "send_back": (frozenset({CaseStatus.REVIEWING}), CaseStatus.TRIAGED),
}

# 每个源状态可执行的动作（用于非法跃迁时给出可区分原因）
_AVAILABLE_ACTIONS: dict[CaseStatus, tuple[str, ...]] = {
    CaseStatus.SUBMITTED: ("assess",),
    CaseStatus.TRIAGED: ("start_review", "recompute"),
    CaseStatus.REVIEWING: ("confirm", "dismiss", "send_back"),
    CaseStatus.CONFIRMED: (),
    CaseStatus.DISMISSED: (),
}

_NUMERIC_FIELDS = ("age", "heart_rate", "systolic_bp", "spo2", "temperature")


class TriageService:
    def __init__(self, repository: CaseRepository | None = None,
                 engine: RuleEngine | None = None) -> None:
        self.repo = repository or CaseRepository()
        self.engine = engine or RuleEngine()

    # ------------------------------------------------------------------ 提交
    def submit_case(self, patient: dict, idempotency_key: str) -> dict:
        """提交病例；相同提交凭据的重复请求被拒绝，不产生重复评估。"""
        self._validate_payload(patient, idempotency_key)

        existing = self.repo.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            raise TriageError(
                ErrorCode.DUPLICATE_SUBMISSION,
                f"提交凭据 {idempotency_key!r} 已对应病例 {existing.case_id}，"
                f"重复请求不会产生重复评估",
            )

        now = utcnow_iso()
        record = CaseRecord(
            case_id=uuid.uuid4().hex[:12],
            idempotency_key=idempotency_key,
            patient=dict(patient),
            status=CaseStatus.SUBMITTED,
            created_at=now,
            updated_at=now,
        )
        self.repo.save(record)  # 全局锁内二次检查凭据
        self._append_audit(record, "SUBMIT", f"病例提交，患者: {patient}")
        return self._snapshot(record)

    def get_case(self, case_id: str) -> dict:
        """查询病例快照（含绑定结论与审核轨迹）。"""
        return self._snapshot(self.repo.get(case_id))

    # ------------------------------------------------------------------ 评估
    def assess_case(self, case_id: str, rule_version: str | None = None) -> dict:
        """评估病例并绑定规则版本。

        - 仅 SUBMITTED 可评估，评估后流转为 TRIAGED；
        - TRIAGED 后以同一版本重复请求：返回同一结论（不重复评估）；
        - TRIAGED 后请求不同版本：拒绝并提示使用 recompute（历史结论不被覆盖）；
        - 同病例并发请求由病例锁串行化，结论唯一。
        """
        record = self.repo.get(case_id)
        with self.repo.lock_for(record.case_id):
            target_version = rule_version or LATEST_VERSION
            rule = get_rule(target_version)

            if record.status is CaseStatus.TRIAGED:
                if record.assessment is not None and \
                        record.assessment.version == target_version:
                    return self._snapshot(record)  # 幂等：结论一致
                raise TriageError(
                    ErrorCode.INVALID_TRANSITION,
                    f"病例已绑定规则版本 {record.assessment.version if record.assessment else 'None'}，"
                    f"不能在评估入口改用 {target_version}；如需复现历史/对比版本请使用 recompute，"
                    f"历史结论不会被追溯改写",
                )
            if record.status is not CaseStatus.SUBMITTED:
                raise TriageError(
                    ErrorCode.INVALID_TRANSITION,
                    f"当前状态 {record.status.value} 不允许评估，"
                    f"仅 SUBMITTED 可评估",
                )

            try:
                assessment = self.engine.evaluate(record.patient, rule)
            except TriageError:
                raise
            except Exception as exc:
                raise normalize_engine_error(exc) from exc

            record.assessment = assessment
            record.history.append(assessment)
            record.status = CaseStatus.TRIAGED
            record.updated_at = utcnow_iso()
            self._append_audit(
                record,
                "ASSESS",
                f"按规则 {target_version} 评估: 等级={assessment.level.value}，"
                f"总分={assessment.score}，判定依据={list(assessment.reasons)}",
                rule_version=target_version,
            )
            return self._snapshot(record)

    def recompute(self, case_id: str, rule_version: str) -> dict:
        """按指定（含历史）规则版本重算。

        只读操作：不覆盖已绑定结论、不改变状态，保证历史结论不被追溯改写。
        """
        record = self.repo.get(case_id)
        rule = get_rule(rule_version)  # 未知版本 -> RULE_VERSION_NOT_FOUND
        try:
            recomputed = self.engine.evaluate(record.patient, rule)
        except Exception as exc:
            raise normalize_engine_error(exc) from exc

        return {
            "case_id": case_id,
            "requested_version": rule_version,
            "recomputed": recomputed.to_dict(),
            "bound_assessment": record.assessment.to_dict()
            if record.assessment else None,
            "rewrites_history": False,
        }

    # -------------------------------------------------------------- 状态流转
    def transition(self, case_id: str, action: str) -> dict:
        """执行审核流转动作；非法跃迁以可区分原因拒绝。"""
        record = self.repo.get(case_id)
        with self.repo.lock_for(record.case_id):
            if action not in ALLOWED_TRANSITIONS:
                allowed = sorted(ALLOWED_TRANSITIONS)
                raise TriageError(
                    ErrorCode.INVALID_TRANSITION,
                    f"未知流转动作 {action!r}，合法动作: {allowed}",
                )

            sources, target = ALLOWED_TRANSITIONS[action]

            if action == "start_review" and record.status is CaseStatus.SUBMITTED:
                raise TriageError(
                    ErrorCode.CASE_NOT_TRIAGED,
                    f"病例尚未评估分诊（状态 {record.status.value}），"
                    f"不能进入审核",
                )

            if record.status not in sources:
                available = _AVAILABLE_ACTIONS.get(record.status, ())
                raise TriageError(
                    ErrorCode.INVALID_TRANSITION,
                    f"非法状态跃迁: 动作 {action!r} 要求源状态 "
                    f"{sorted(s.value for s in sources)}，当前为 "
                    f"{record.status.value}；当前状态允许的动作: {list(available)}",
                )

            record.status = target
            record.updated_at = utcnow_iso()
            self._append_audit(
                record,
                action.upper(),
                f"状态流转 -> {target.value}",
                rule_version=record.assessment.version if record.assessment else None,
            )
            return self._snapshot(record)

    # ------------------------------------------------------------------ 内部
    def _validate_payload(self, patient: object, idempotency_key: object) -> None:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise TriageError(
                ErrorCode.INVALID_PAYLOAD, "idempotency_key 必须为非空字符串"
            )
        if not isinstance(patient, dict) or not patient:
            raise TriageError(
                ErrorCode.INVALID_PAYLOAD, "patient 必须为非空对象"
            )
        for field in _NUMERIC_FIELDS:
            if field in patient and not isinstance(
                patient[field], numbers.Real
            ) or field in patient and isinstance(patient[field], bool):
                raise TriageError(
                    ErrorCode.INVALID_PAYLOAD,
                    f"patient.{field} 必须为数值，实际为 {type(patient[field]).__name__}",
                )
        if "consciousness" in patient and not isinstance(
            patient["consciousness"], str
        ):
            raise TriageError(
                ErrorCode.INVALID_PAYLOAD, "patient.consciousness 必须为字符串"
            )
        if "comorbidities" in patient and not isinstance(
            patient["comorbidities"], (list, tuple)
        ):
            raise TriageError(
                ErrorCode.INVALID_PAYLOAD, "patient.comorbidities 必须为数组"
            )

    def _append_audit(self, record: CaseRecord, action: str, detail: str,
                      rule_version: str | None = None) -> None:
        from app.models import AuditEvent

        record.audit_seq += 1
        record.audit.append(
            AuditEvent(
                seq=record.audit_seq,
                timestamp=utcnow_iso(),
                action=action,
                detail=detail,
                rule_version=rule_version,
            )
        )

    def _snapshot(self, record: CaseRecord) -> dict:
        return record.snapshot()
