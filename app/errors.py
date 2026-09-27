"""归一化对外错误类型。

内部异常（规则配置错误、引擎内部错误）一律映射为 TriageError，
不向外透出内部堆栈或实现细节。
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    CASE_NOT_TRIAGED = "CASE_NOT_TRIAGED"
    RULE_VERSION_NOT_FOUND = "RULE_VERSION_NOT_FOUND"
    RULE_CONFIG_ERROR = "RULE_CONFIG_ERROR"
    ENGINE_INTERNAL_ERROR = "ENGINE_INTERNAL_ERROR"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"


class TriageError(Exception):
    """对外暴露的归一化业务错误。"""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"TriageError(code={self.code.value}, message={self.message!r})"


def normalize_engine_error(exc: Exception) -> TriageError:
    """把规则引擎内部异常映射为可区分的归一化错误。

    - RuleConfigError      -> RULE_CONFIG_ERROR（规则配置缺失/非法）
    - EngineInternalError  -> ENGINE_INTERNAL_ERROR（引擎计算内部异常）
    - 其他未知异常          -> ENGINE_INTERNAL_ERROR（吞掉细节）
    """
    from app.engine import EngineInternalError, RuleConfigError

    if isinstance(exc, RuleConfigError):
        return TriageError(ErrorCode.RULE_CONFIG_ERROR, str(exc))
    if isinstance(exc, EngineInternalError):
        return TriageError(ErrorCode.ENGINE_INTERNAL_ERROR, str(exc))
    return TriageError(ErrorCode.ENGINE_INTERNAL_ERROR, "规则引擎内部错误")
