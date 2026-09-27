"""决策规则引擎：多版本管理、评估与结论复现。

规则版本是不可变对象；评估为纯函数，同一病例 + 同一规则版本
必然得到一致结论，历史结论不会被规则更新追溯改写。
"""
from __future__ import annotations

import threading

from .errors import RuleConfigError, RuleEvaluationError, RuleVersionNotFoundError
from .models import Assessment, PatientCase, RiskLevel, RuleVersion

REQUIRED_THRESHOLDS = ("age", "systolic_bp", "heart_rate", "temperature_c", "spo2")
REQUIRED_CUTOFFS = ("MEDIUM", "HIGH", "CRITICAL")


def _validate_rule(rule: RuleVersion) -> None:
    """校验规则配置完整性，配置错误归一化为 RuleConfigError。"""
    missing = [k for k in REQUIRED_THRESHOLDS if k not in rule.thresholds]
    if missing:
        raise RuleConfigError(f"规则 {rule.version} 缺少阈值: {missing}")
    missing_cuts = [k for k in REQUIRED_CUTOFFS if k not in rule.level_cutoffs]
    if missing_cuts:
        raise RuleConfigError(f"规则 {rule.version} 缺少分级截断: {missing_cuts}")
    cuts = rule.level_cutoffs
    if not (cuts["MEDIUM"] < cuts["HIGH"] < cuts["CRITICAL"]):
        raise RuleConfigError(f"规则 {rule.version} 分级截断必须严格递增")


def _evaluate_with_rule(case: PatientCase, rule: RuleVersion) -> Assessment:
    """纯函数评估：输出分数、风险等级与逐条判定依据。"""
    _validate_rule(rule)
    t = rule.thresholds
    score = 0.0
    rationale: list[str] = []

    def add(condition: bool, points: float, reason: str) -> None:
        nonlocal score
        if condition:
            score += points
            rationale.append(f"+{points:g} 分: {reason}")

    add(case.age >= t["age"], 2.0, f"年龄 {case.age} >= 阈值 {t['age']:g}")
    add(
        case.systolic_bp >= t["systolic_bp"],
        2.0,
        f"收缩压 {case.systolic_bp} >= 阈值 {t['systolic_bp']:g}",
    )
    add(
        case.heart_rate >= t["heart_rate"],
        2.0,
        f"心率 {case.heart_rate} >= 阈值 {t['heart_rate']:g}",
    )
    add(
        case.temperature_c >= t["temperature_c"],
        1.5,
        f"体温 {case.temperature_c} >= 阈值 {t['temperature_c']:g}",
    )
    add(
        case.spo2 <= t["spo2"],
        2.5,
        f"血氧 {case.spo2} <= 阈值 {t['spo2']:g}",
    )
    for symptom in case.symptoms:
        weight = rule.symptom_weights.get(symptom, 0.0)
        if weight > 0:
            score += weight
            rationale.append(f"+{weight:g} 分: 症状 {symptom}")

    cuts = rule.level_cutoffs
    if score >= cuts["CRITICAL"]:
        level = RiskLevel.CRITICAL
    elif score >= cuts["HIGH"]:
        level = RiskLevel.HIGH
    elif score >= cuts["MEDIUM"]:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW
    rationale.append(
        f"总分 {score:g}，截断 MEDIUM={cuts['MEDIUM']:g} "
        f"HIGH={cuts['HIGH']:g} CRITICAL={cuts['CRITICAL']:g} -> {level.value}"
    )
    return Assessment(
        case_id=case.case_id,
        rule_version=rule.version,
        risk_level=level,
        score=score,
        rationale=tuple(rationale),
    )


class RuleRegistry:
    """规则版本注册表：版本不可变、不可覆盖。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: dict[str, RuleVersion] = {}

    def register(self, rule: RuleVersion) -> None:
        _validate_rule(rule)
        with self._lock:
            if rule.version in self._rules:
                raise RuleConfigError(f"规则版本 {rule.version} 已存在，不可覆盖")
            self._rules[rule.version] = rule

    def get(self, version: str) -> RuleVersion:
        try:
            return self._rules[version]
        except KeyError:
            raise RuleVersionNotFoundError(f"规则版本不存在: {version}") from None

    def latest(self) -> RuleVersion:
        with self._lock:
            if not self._rules:
                raise RuleConfigError("尚未注册任何规则版本")
            return max(self._rules.values(), key=lambda r: r.created_at)

    def evaluate(
        self, case: PatientCase, version: str | None = None
    ) -> Assessment:
        rule = self.get(version) if version else self.latest()
        try:
            return _evaluate_with_rule(case, rule)
        except RuleConfigError:
            raise
        except Exception as exc:  # 内部异常归一化，不直接透出
            raise RuleEvaluationError(
                f"规则 {rule.version} 评估失败: {type(exc).__name__}"
            ) from exc
