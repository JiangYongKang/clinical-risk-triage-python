"""内存病例仓储。

每个病例一把锁：同一病例的并发评估/流转串行化，
不同病例互不阻塞；全局锁仅保护仓储结构与锁表的变更。
"""
from __future__ import annotations

import threading

from app.errors import ErrorCode, TriageError
from app.models import CaseRecord


class CaseRepository:
    def __init__(self) -> None:
        self._cases: dict[str, CaseRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._global_lock = threading.Lock()
        self._case_locks: dict[str, threading.Lock] = {}

    def lock_for(self, case_id: str) -> threading.Lock:
        """获取某病例的专用锁（用于并发评估串行化）。"""
        with self._global_lock:
            lock = self._case_locks.get(case_id)
            if lock is None:
                lock = threading.Lock()
                self._case_locks[case_id] = lock
            return lock

    def get(self, case_id: str) -> CaseRecord:
        """按 ID 获取病例，不存在抛 CASE_NOT_FOUND。"""
        record = self._cases.get(case_id)
        if record is None:
            raise TriageError(ErrorCode.CASE_NOT_FOUND, f"病例不存在: {case_id}")
        return record

    def find_by_idempotency_key(self, key: str) -> CaseRecord | None:
        """按提交凭据查找病例。"""
        case_id = self._idempotency.get(key)
        if case_id is None:
            return None
        return self._cases.get(case_id)

    def save(self, record: CaseRecord) -> None:
        """新增病例并登记提交凭据；凭据重复时拒绝。"""
        with self._global_lock:
            if record.idempotency_key in self._idempotency:
                existing = self._idempotency[record.idempotency_key]
                raise TriageError(
                    ErrorCode.DUPLICATE_SUBMISSION,
                    f"提交凭据重复: {record.idempotency_key}，"
                    f"已存在病例 {existing}，不产生重复评估",
                )
            self._cases[record.case_id] = record
            self._idempotency[record.idempotency_key] = record.case_id
