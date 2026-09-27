"""规则版本切换、结论复现与错误归一化。"""
import logging

import pytest

from app.errors import RuleConfigError, RuleVersionNotFoundError
from app.models import PatientCase, RiskLevel, RuleVersion
from tests.conftest import make_rule

log = logging.getLogger(__name__)


def test_assessment_binds_rule_version(service, sample_case):
    service.submit_case("k-1", sample_case)
    a = service.evaluate_case(sample_case.case_id, rule_version="v1")
    log.info("输入: %s 规则版本 v1", sample_case)
    log.info("判定依据: %s", list(a.rationale))
    assert a.rule_version == "v1"
    assert a.risk_level == RiskLevel.CRITICAL


def test_rule_update_does_not_rewrite_history(service, rules, sample_case):
    service.submit_case("k-2", sample_case)
    before = service.evaluate_case(sample_case.case_id, rule_version="v1")
    # 注册新版本：年龄阈值提高，结论应变化
    rules.register(make_rule("v2", age_threshold=200))
    recomputed_v2 = rules.evaluate(sample_case, "v2")
    log.info("v1 结论: %s %.1f; v2 结论: %s %.1f",
             before.risk_level, before.score,
             recomputed_v2.risk_level, recomputed_v2.score)
    assert recomputed_v2.score == before.score - 2.0  # 年龄分不再计入
    # 历史结论不被追溯改写
    after = service._store.get_assessment(sample_case.case_id)
    assert after == before and after.rule_version == "v1"


def test_recompute_with_old_version_reproducible(rules, sample_case):
    rules.register(make_rule("v2", age_threshold=200))
    a1 = rules.evaluate(sample_case, "v1")
    a2 = rules.evaluate(sample_case, "v1")
    log.info("按旧版本 v1 重算: %s vs %s", a1.risk_level, a2.risk_level)
    assert a1.risk_level == a2.risk_level
    assert a1.score == a2.score
    assert a1.rationale == a2.rationale


def test_unknown_rule_version(service, sample_case):
    service.submit_case("k-3", sample_case)
    with pytest.raises(RuleVersionNotFoundError) as exc:
        service.evaluate_case(sample_case.case_id, rule_version="vX")
    assert exc.value.code == "RULE_VERSION_NOT_FOUND"
    log.info("判定依据: 版本 vX 未注册 -> %s", exc.value.code)


def test_rule_config_error_normalized(rules, sample_case):
    bad = RuleVersion(
        version="bad",
        thresholds={"age": 65},  # 缺少其余必需阈值
        symptom_weights={},
        level_cutoffs={"MEDIUM": 2.0, "HIGH": 5.0, "CRITICAL": 8.0},
    )
    with pytest.raises(RuleConfigError) as exc:
        rules.register(bad)
    assert exc.value.code == "RULE_CONFIG_ERROR"
    log.info("判定依据: 配置缺失归一化为 %s: %s", exc.value.code, exc.value)


def test_rule_version_immutable(rules):
    with pytest.raises(RuleConfigError):
        rules.register(make_rule("v1"))  # 重复注册同版本被拒绝
    log.info("判定依据: 规则版本不可覆盖")
