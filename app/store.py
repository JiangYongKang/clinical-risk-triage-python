"""线程安全的内存存储：幂等提交、状态机流转、评估存取。

- 同一 idempotency_key 重复提交返回首次结果，不产生重复病例；
  同 key 不同载荷则报 DuplicateSubmissionError（可区分原因）。
- 状态流转仅允许 ALLOWED_TRANSITIONS 中的跃迁。
- 同一病例的评估只保存一次，并发下结论唯一。
"""
from __future__ import annotations

import threading

from .errors import (
    CaseNotFoundError,
    DuplicateSubmissionError,
    IllegalTransitionError,
)
from .models import (
    ALLOWED_TRANSITIONS,
    Assessment,
    AssessmentKind,
    AssessmentRecord,
    CaseStatus,
    PatientCase,
)


class CaseStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cases: dict[str, PatientCase] = {}
        self._status: dict[str, CaseStatus] = {}
        self._assessments: dict[str, Assessment] = {}
        self._idempotency: dict[str, str] = {}  # key -> case_id
        self._history: dict[str, list[AssessmentRecord]] = {}

    def submit(self, idempotency_key: str, case: PatientCase) -> PatientCase:
        with self._lock:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id is not None:
                existing = self._cases[existing_id]
                if existing == case:
                    return existing  # 幂等重放：返回首次结果
                raise DuplicateSubmissionError(
                    f"提交凭据 {idempotency_key} 已绑定病例 {existing_id}，"
                    f"与本次载荷不一致"
                )
            self._idempotency[idempotency_key] = case.case_id
            self._cases[case.case_id] = case
            self._status[case.case_id] = CaseStatus.SUBMITTED
            return case

    def get_case(self, case_id: str) -> PatientCase:
        with self._lock:
            try:
                return self._cases[case_id]
            except KeyError:
                raise CaseNotFoundError(f"病例不存在: {case_id}") from None

    def get_status(self, case_id: str) -> CaseStatus:
        with self._lock:
            try:
                return self._status[case_id]
            except KeyError:
                raise CaseNotFoundError(f"病例不存在: {case_id}") from None

    def transition(self, case_id: str, target: CaseStatus) -> CaseStatus:
        with self._lock:
            current = self.get_status(case_id)
            if target not in ALLOWED_TRANSITIONS[current]:
                raise IllegalTransitionError(current.value, target.value)
            self._status[case_id] = target
            return target

    def save_assessment(self, assessment: Assessment) -> Assessment:
        """同一病例只保留首次评估结论，并发重复评估返回既有结论。"""
        with self._lock:
            existing = self._assessments.get(assessment.case_id)
            if existing is not None:
                return existing
            self._assessments[assessment.case_id] = assessment
            return assessment

    def get_assessment(self, case_id: str) -> Assessment:
        with self._lock:
            try:
                return self._assessments[case_id]
            except KeyError:
                raise CaseNotFoundError(
                    f"病例 {case_id} 尚无评估结论"
                ) from None

    def append_record(
        self, case_id: str, kind: AssessmentKind, assessment: Assessment
    ) -> AssessmentRecord:
        """追加一条评估轨迹记录：只增不改，序号按发生顺序单调递增。"""
        with self._lock:
            records = self._history.setdefault(case_id, [])
            record = AssessmentRecord(
                seq=len(records) + 1, kind=kind, assessment=assessment
            )
            records.append(record)
            return record

    def get_history(self, case_id: str) -> tuple[AssessmentRecord, ...]:
        """按发生顺序返回完整轨迹；病例不存在则报错，无记录返回空。"""
        with self._lock:
            self.get_case(case_id)  # 复用存在性校验
            return tuple(self._history.get(case_id, ()))
