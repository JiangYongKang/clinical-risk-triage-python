"""可区分的错误类型：对外接口不直接透出内部异常。"""
from __future__ import annotations


class TriageError(Exception):
    """服务层错误基类。"""

    code: str = "TRIAGE_ERROR"


class DuplicateSubmissionError(TriageError):
    """同一提交凭据的重复请求（幂等冲突）。"""

    code = "DUPLICATE_SUBMISSION"


class IllegalTransitionError(TriageError):
    """非法状态跃迁。"""

    code = "ILLEGAL_TRANSITION"

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"非法状态跃迁: {current} -> {target}")
        self.current = current
        self.target = target


class CaseNotFoundError(TriageError):
    code = "CASE_NOT_FOUND"


class RuleVersionNotFoundError(TriageError):
    code = "RULE_VERSION_NOT_FOUND"


class RuleConfigError(TriageError):
    """规则配置错误（依赖异常归一化）。"""

    code = "RULE_CONFIG_ERROR"


class RuleEvaluationError(TriageError):
    """规则计算内部异常（归一化包装）。"""

    code = "RULE_EVALUATION_ERROR"
