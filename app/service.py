"""分诊服务编排：提交、评估、重算、流转与并发归一化。

- evaluate_case 对同一病例加锁，并发请求共享同一结论；
- 评估成功后自动将 SUBMITTED -> EVALUATED，并写入评估轨迹；
- reassess_case 按指定版本只读重算：不覆盖绑定结论，仅追加轨迹；
- 规则层的配置错误/内部异常以可区分类型向上传递。
"""
from __future__ import annotations

import threading

from .errors import CaseNotFoundError
from .models import (
    Assessment,
    AssessmentKind,
    AssessmentRecord,
    CaseStatus,
    PatientCase,
)
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
            except CaseNotFoundError:
                pass
            assessment = self._rules.evaluate(case, rule_version)
            saved = self._store.save_assessment(assessment)
            if saved is assessment:
                self._store.append_record(
                    case_id, AssessmentKind.EVALUATE, saved
                )
            if self._store.get_status(case_id) == CaseStatus.SUBMITTED:
                self._store.transition(case_id, CaseStatus.EVALUATED)
            return saved

    def reassess_case(self, case_id: str, rule_version: str) -> Assessment:
        """按指定规则版本只读重算，复现/核对当时结论。

        - 版本未注册 -> RuleVersionNotFoundError；
        - 允许与病例绑定版本不一致：调用方通过返回结论的 rule_version
          与绑定结论比对区分，响应中显式标注是否一致；
        - 只读：不覆盖病例已绑定的结论，仅在轨迹中追加 REASSESS 记录。
        """
        with self._lock_for(case_id):
            self._store.get_assessment(case_id)  # 须已存在正式评估结论
            case = self._store.get_case(case_id)
            recomputed = self._rules.evaluate(case, rule_version)
            self._store.append_record(
                case_id, AssessmentKind.REASSESS, recomputed
            )
            return recomputed

    def get_assessment(self, case_id: str) -> Assessment:
        """病例当前绑定的正式评估结论。"""
        return self._store.get_assessment(case_id)

    def get_history(self, case_id: str) -> tuple[AssessmentRecord, ...]:
        return self._store.get_history(case_id)

    def transition_case(self, case_id: str, target: CaseStatus) -> CaseStatus:
        return self._store.transition(case_id, target)
