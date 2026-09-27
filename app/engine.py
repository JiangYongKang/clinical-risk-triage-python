"""规则引擎：依据指定规则版本计算风险等级与判定依据。"""
from __future__ import annotations

from collections.abc import Callable

from app.models import RiskAssessment, RiskFactor, RiskLevel, RuleSet, utcnow_iso


class RuleConfigError(Exception):
    """规则配置错误（如引用了未知因子、阈值非法）。"""


class EngineInternalError(Exception):
    """引擎计算过程中的内部异常。"""


# 因子判定器：因子编码 -> 判定函数(patient) -> bool
_FACTOR_EVALUATORS: dict[str, Callable[[dict], bool]] = {
    "AGE_GE_65": lambda p: p.get("age", 0) >= 65,
    "AGE_GE_80": lambda p: p.get("age", 0) >= 80,
    "HR_TACHY": lambda p: p.get("heart_rate", 0) > 110,
    "SBP_LOW": lambda p: p.get("systolic_bp", 999) < 90,
    "SPO2_LOW": lambda p: p.get("spo2", 100) < 92,
    "TEMP_HIGH": lambda p: p.get("temperature", 0) >= 39,
    "COMORBID": lambda p: bool(p.get("comorbidities")),
    "CONSCIOUSNESS": lambda p: p.get("consciousness") == "impaired",
}


class RuleEngine:
    """无状态规则引擎，同一输入 + 同一规则版本 => 同一结论。"""

    def evaluate(self, patient: dict, rule: RuleSet) -> RiskAssessment:
        """按给定规则版本评估病例，返回绑定规则版本的结论。"""
        try:
            score, matched = self._score(patient, rule)
            level = self._level_for(score, rule)
        except RuleConfigError:
            raise
        except Exception as exc:  # 归一化为内部异常，细节不透出
            raise EngineInternalError(f"规则计算失败: {type(exc).__name__}") from exc

        reasons = [f"命中因子 {f.label}({f.code}) +{f.weight}" for f in matched]
        reasons.append(
            f"总分 {score}，阈值 MEDIUM/HIGH/CRITICAL="
            f"{rule.thresholds}，判定为 {level.value}"
        )
        return RiskAssessment(
            version=rule.version,
            level=level,
            score=score,
            matched=tuple(matched),
            reasons=tuple(reasons),
            computed_at=utcnow_iso(),
        )

    def _score(self, patient: dict, rule: RuleSet) -> tuple[int, list[RiskFactor]]:
        """计算分数与命中因子；规则引用未知因子视为配置错误。"""
        score = 0
        matched: list[RiskFactor] = []
        for factor in rule.factors:
            evaluator = _FACTOR_EVALUATORS.get(factor.code)
            if evaluator is None:
                raise RuleConfigError(
                    f"规则 {rule.version} 引用了未注册的因子: {factor.code}"
                )
            if evaluator(patient):
                score += factor.weight
                matched.append(factor)
        return score, matched

    def _level_for(self, score: int, rule: RuleSet) -> RiskLevel:
        """分数映射风险等级；阈值必须单调递增，否则视为配置错误。"""
        medium, high, critical = rule.thresholds
        if not (0 < medium <= high <= critical):
            raise RuleConfigError(
                f"规则 {rule.version} 阈值非法: {rule.thresholds}"
            )
        if score >= critical:
            return RiskLevel.CRITICAL
        if score >= high:
            return RiskLevel.HIGH
        if score >= medium:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
