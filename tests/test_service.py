"""服务层测试：重复提交、非法流转、版本切换、可解释性。"""
from __future__ import annotations

import pytest

from app.errors import ErrorCode, TriageError
from app.models import CaseStatus
from tests.conftest import log_assessment, logger

PATIENT = {
    "age": 70,
    "heart_rate": 115,
    "systolic_bp": 88,
    "spo2": 90,
    "temperature": 39.2,
    "comorbidities": ["diabetes"],
    "consciousness": "clear",
}


def _expect_error(service_call, code: ErrorCode):
    with pytest.raises(TriageError) as exc_info:
        service_call()
    assert exc_info.value.code is code
    logger.info("预期错误: code=%s message=%s", code.value, exc_info.value.message)
    return exc_info.value


def test_duplicate_submission_rejected_and_no_duplicate_assessment(service):
    first = service.submit_case(PATIENT, "idem-001")
    assessed = service.assess_case(first["case_id"])
    log_assessment("首次提交并评估", PATIENT, assessed)

    err = _expect_error(
        lambda: service.submit_case(PATIENT, "idem-001"),
        ErrorCode.DUPLICATE_SUBMISSION,
    )
    assert "idem-001" in err.message

    # 只有一份病例、一条历史结论
    assert assessed["history"] and len(assessed["history"]) == 1


def test_assess_is_idempotent_for_same_version(service):
    submitted = service.submit_case({"age": 70, "spo2": 90}, "idem-002")
    first = service.assess_case(submitted["case_id"], "v2.0")
    second = service.assess_case(submitted["case_id"], "v2.0")
    log_assessment("同版本重复评估（幂等）", {"age": 70, "spo2": 90}, second)

    assert second["assessment"] == first["assessment"]
    assert len(second["history"]) == 1
    assert second["status"] == CaseStatus.TRIAGED.value


def test_illegal_transitions_have_distinguishable_reasons(service):
    cid = service.submit_case(PATIENT, "idem-003")["case_id"]

    # 1) SUBMITTED 直接 start_review -> CASE_NOT_TRIAGED（原因可区分）
    _expect_error(
        lambda: service.transition(cid, "start_review"),
        ErrorCode.CASE_NOT_TRIAGED,
    )
    service.assess_case(cid)

    # 2) TRIAGED 上 confirm -> INVALID_TRANSITION，信息提示合法动作
    err = _expect_error(
        lambda: service.transition(cid, "confirm"),
        ErrorCode.INVALID_TRANSITION,
    )
    assert "start_review" in err.message

    # 3) 未知动作 -> INVALID_TRANSITION，信息说明动作未知
    err = _expect_error(
        lambda: service.transition(cid, "explode"),
        ErrorCode.INVALID_TRANSITION,
    )
    assert "未知流转动作" in err.message

    service.transition(cid, "start_review")
    service.transition(cid, "confirm")

    # 4) CONFIRMED 终态再流转 -> INVALID_TRANSITION
    _expect_error(
        lambda: service.transition(cid, "dismiss"),
        ErrorCode.INVALID_TRANSITION,
    )


def test_legal_transition_chain(service):
    cid = service.submit_case(PATIENT, "idem-004")["case_id"]
    service.assess_case(cid)
    reviewing = service.transition(cid, "start_review")
    assert reviewing["status"] == CaseStatus.REVIEWING.value
    sent_back = service.transition(cid, "send_back")
    assert sent_back["status"] == CaseStatus.TRIAGED.value
    service.transition(cid, "start_review")
    confirmed = service.transition(cid, "dismiss")
    assert confirmed["status"] == CaseStatus.DISMISSED.value
    logger.info("流转链路审核轨迹: %s", confirmed["audit"])


def test_rule_version_switch_changes_result_but_keeps_history(service):
    patient = {"age": 70, "spo2": 90}  # v1: 2+3=5 MEDIUM; v2: 2+4=6 HIGH
    cid = service.submit_case(patient, "idem-005")["case_id"]

    bound_v2 = service.assess_case(cid, "v2.0")
    log_assessment("以 v2.0 评估并绑定", patient, bound_v2)
    assert bound_v2["assessment"]["rule_version"] == "v2.0"
    assert bound_v2["assessment"]["level"] == "HIGH"
    assert bound_v2["assessment"]["score"] == 6

    # 评估入口不允许换版本（防止历史被追溯改写）
    _expect_error(
        lambda: service.assess_case(cid, "v1.0"),
        ErrorCode.INVALID_TRANSITION,
    )

    # 按旧版本重算 -> 得到旧版本应有结果，且不覆盖绑定结论
    recomputed = service.recompute(cid, "v1.0")
    logger.info("按 v1.0 重算结果: %s", recomputed["recomputed"])
    assert recomputed["recomputed"]["level"] == "MEDIUM"
    assert recomputed["recomputed"]["score"] == 5
    assert recomputed["rewrites_history"] is False

    still_bound = service.get_case(cid)
    assert still_bound["assessment"]["rule_version"] == "v2.0"
    assert still_bound["assessment"]["level"] == "HIGH"


def test_recompute_same_version_is_reproducible(service):
    cid = service.submit_case(PATIENT, "idem-006")["case_id"]
    bound = service.assess_case(cid, "v1.0")
    log_assessment("以 v1.0 评估并绑定", PATIENT, bound)
    again = service.recompute(cid, "v1.0")
    assert again["recomputed"]["level"] == bound["assessment"]["level"]
    assert again["recomputed"]["score"] == bound["assessment"]["score"]
    # computed_at 以外字段完全一致
    assert again["recomputed"]["matched_factors"] == \
        bound["assessment"]["matched_factors"]
    assert again["recomputed"]["reasons"] == bound["assessment"]["reasons"]


def test_assessment_reasons_are_explainable(service):
    patient = {"age": 81, "spo2": 90}
    cid = service.submit_case(patient, "idem-007")["case_id"]
    snap = service.assess_case(cid, "v2.0")
    log_assessment("可解释性检查", patient, snap)
    reasons = snap["assessment"]["reasons"]
    assert any("AGE_GE_80" in r for r in reasons)
    assert any("SPO2_LOW" in r for r in reasons)
    assert any("判定为" in r and "HIGH" in r for r in reasons)
    # 审核轨迹记录了评估动作与规则版本
    assess_events = [e for e in snap["audit"] if e["action"] == "ASSESS"]
    assert assess_events and assess_events[-1]["rule_version"] == "v2.0"
