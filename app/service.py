"""分诊服务编排：提交、评估、流转与并发归一化。

- evaluate_case 对同一病例加锁，并发请求共享同一结论；
- 评估成功后自动将 SUBMITTED -> EVALUATED；
- 规则层的配置错误/内部异常以可区分类型向上传递。
"""
from __future__ import annotations

import threading

from .models import Assessment, CaseStatus, PatientCase
from .rules import RuleRegistry
from .store import CaseStore


class TriageService:
    def __init__(self, store: CaseStore, rules: RuleRegistry) -> None:
        self._store = store
        self._rules = rules
        self._eval_locks: dict[str, threading.Lock] = {}
        self._eval_locks_guard = threading.Lock()

    def _lock_for(self, case_id: str) -> threading.Lock:
        with self._eval_locks_guard:
            return self._eval_locks.setdefault(case_id, threading.Lock())

    def submit_case(
        self, idempotency_key: str, case: PatientCase
    ) -> PatientCase:
        return self._store.submit(idempotency_key, case)

    def evaluate_case(
        self, case_id: str, rule_version: str | None = None
    ) -> Assessment:
        case = self._store.get_case(case_id)
        with self._lock_for(case_id):
            # 双重检查：并发下复用既有结论，保证结论唯一且一致
            try:
                return self._store.get_assessment(case_id)
            except Exception:
                pass
            assessment = self._rules.evaluate(case, rule_version)
            saved = self._store.save_assessment(assessment)
            if self._store.get_status(case_id) == CaseStatus.SUBMITTED:
                self._store.transition(case_id, CaseStatus.EVALUATED)
            return saved

    def transition_case(self, case_id: str, target: CaseStatus) -> CaseStatus:
        return self._store.transition(case_id, target)
