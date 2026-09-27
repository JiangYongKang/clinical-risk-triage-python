"""错误归一化测试：配置错误与内部异常映射为可区分类型，不透出内部细节。"""
from __future__ import annotations

import pytest

from app import engine as engine_module
from app import rules as rules_module
from app.engine import EngineInternalError, RuleConfigError, RuleEngine
from app.errors import ErrorCode, TriageError, normalize_engine_error
from app.models import RiskFactor, RuleSet
from tests.conftest import logger


def test_normalize_engine_error_mapping():
    cfg = normalize_engine_error(RuleConfigError("阈值非法"))
    assert cfg.code is ErrorCode.RULE_CONFIG_ERROR

    internal = normalize_engine_error(EngineInternalError("规则计算失败"))
    assert internal.code is ErrorCode.ENGINE_INTERNAL_ERROR

    unknown = normalize_engine_error(RuntimeError("some secret internals"))
    assert unknown.code is ErrorCode.ENGINE_INTERNAL_ERROR
    assert "secret" not in unknown.message, "未知内部异常细节不得透出"
    logger.info("未知异常归一化: %s", unknown.message)


def test_unknown_factor_maps_to_rule_config_error():
    engine = RuleEngine()
    bad_rule = RuleSet("v-bad", "坏规则", (3, 6, 9),
                       (RiskFactor("GHOST_FACTOR", "幽灵因子", 1),))
    with pytest.raises(RuleConfigError):
        engine.evaluate({"age": 70}, bad_rule)
    err = normalize_engine_error(_capture(engine, bad_rule))
    assert err.code is ErrorCode.RULE_CONFIG_ERROR
    assert "GHOST_FACTOR" in err.message
    logger.info("配置错误: %s", err.message)


def test_invalid_thresholds_map_to_rule_config_error():
    engine = RuleEngine()
    bad_rule = RuleSet("v-bad-th", "阈值倒挂", (9, 6, 3), ())
    err = normalize_engine_error(_capture(engine, bad_rule))
    assert err.code is ErrorCode.RULE_CONFIG_ERROR
    logger.info("阈值配置错误: %s", err.message)


def test_unexpected_evaluator_exception_maps_to_internal_error(monkeypatch):
    def boom(_patient: dict) -> bool:
        raise ZeroDivisionError("division by zero in vendor lib")

    monkeypatch.setitem(engine_module._FACTOR_EVALUATORS, "BOOM", boom)
    engine = RuleEngine()
    rule = RuleSet("v-boom", "会炸的规则", (3, 6, 9),
                   (RiskFactor("BOOM", "爆炸因子", 1),))
    err = normalize_engine_error(_capture(engine, rule))
    assert err.code is ErrorCode.ENGINE_INTERNAL_ERROR
    assert "vendor lib" not in err.message, "内部异常细节不得透出"
    logger.info("内部异常归一化: %s", err.message)


def test_service_maps_broken_rule_version(service, monkeypatch):
    cid = service.submit_case({"age": 70}, "err-001")["case_id"]
    broken = RuleSet("v-broken", "引用未知因子的版本", (3, 6, 9),
                     (RiskFactor("MISSING_FACTOR", "缺失因子", 1),))
    monkeypatch.setitem(rules_module.RULE_VERSIONS, "v-broken", broken)

    with pytest.raises(TriageError) as exc_info:
        service.assess_case(cid, "v-broken")
    assert exc_info.value.code is ErrorCode.RULE_CONFIG_ERROR
    logger.info("服务层配置错误: %s", exc_info.value.message)

    # 失败的评估不得改变病例状态
    assert service.get_case(cid)["status"] == "SUBMITTED"


def test_unknown_rule_version_distinguishable(service):
    cid = service.submit_case({"age": 70}, "err-002")["case_id"]
    with pytest.raises(TriageError) as exc_info:
        service.assess_case(cid, "v9.9")
    assert exc_info.value.code is ErrorCode.RULE_VERSION_NOT_FOUND
    assert exc_info.value.code is not ErrorCode.RULE_CONFIG_ERROR
    logger.info("版本不存在: %s", exc_info.value.message)


def test_invalid_payload_rejected(service):
    with pytest.raises(TriageError) as exc_info:
        service.submit_case({"age": "七十"}, "err-003")
    assert exc_info.value.code is ErrorCode.INVALID_PAYLOAD

    with pytest.raises(TriageError) as exc_info:
        service.submit_case({"age": 70}, "")
    assert exc_info.value.code is ErrorCode.INVALID_PAYLOAD


def _capture(engine: RuleEngine, rule: RuleSet) -> Exception:
    try:
        engine.evaluate({"age": 70}, rule)
    except Exception as exc:  # noqa: BLE001 - 测试需要捕获后归一化
        return exc
    raise AssertionError("预期抛出异常")
