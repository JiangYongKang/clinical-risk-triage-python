"""重复提交与非法状态流转。"""
import logging

import pytest

from app.errors import DuplicateSubmissionError, IllegalTransitionError
from app.models import CaseStatus, PatientCase

log = logging.getLogger(__name__)


def test_duplicate_submission_idempotent(service, sample_case):
    log.info("输入: key=k-1 case=%s", sample_case)
    first = service.submit_case("k-1", sample_case)
    again = service.submit_case("k-1", sample_case)
    assert again == first
    assert service._store.get_status(sample_case.case_id) == CaseStatus.SUBMITTED
    log.info("判定依据: 同 key 同载荷 -> 幂等返回首次结果，无重复病例")


def test_duplicate_submission_conflicting_payload(service, sample_case):
    service.submit_case("k-2", sample_case)
    other = PatientCase("case-2", 40, 120, 80, 36.5, 98.0, ())
    log.info("输入: key=k-2 已绑定 %s, 再次提交 %s", sample_case.case_id, other)
    with pytest.raises(DuplicateSubmissionError) as exc:
        service.submit_case("k-2", other)
    assert exc.value.code == "DUPLICATE_SUBMISSION"
    log.info("判定依据: 同 key 不同载荷 -> %s", exc.value)


def test_illegal_transition_rejected(service, sample_case):
    service.submit_case("k-3", sample_case)
    log.info("输入: 状态 SUBMITTED, 目标 CLOSED")
    with pytest.raises(IllegalTransitionError) as exc:
        service.transition_case(sample_case.case_id, CaseStatus.CLOSED)
    assert exc.value.code == "ILLEGAL_TRANSITION"
    assert exc.value.current == "SUBMITTED" and exc.value.target == "CLOSED"
    log.info("判定依据: %s -> %s 不在合法流转表", exc.value.current,
             exc.value.target)


def test_legal_transition_chain(service, sample_case):
    service.submit_case("k-4", sample_case)
    service.evaluate_case(sample_case.case_id)
    assert service._store.get_status(sample_case.case_id) == CaseStatus.EVALUATED
    service.transition_case(sample_case.case_id, CaseStatus.REVIEWED)
    service.transition_case(sample_case.case_id, CaseStatus.CLOSED)
    assert service._store.get_status(sample_case.case_id) == CaseStatus.CLOSED
    log.info("判定依据: SUBMITTED->EVALUATED->REVIEWED->CLOSED 全部合法")
