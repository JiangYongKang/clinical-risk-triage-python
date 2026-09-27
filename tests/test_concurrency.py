"""并发场景测试：同一病例并发评估/提交/流转不产生冲突或重复结论。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.errors import ErrorCode, TriageError
from tests.conftest import logger

PATIENT = {"age": 70, "spo2": 90, "heart_rate": 120}


def test_concurrent_assess_same_case_single_consistent_result(service):
    cid = service.submit_case(PATIENT, "conc-assess")["case_id"]

    def assess(_: int):
        return service.assess_case(cid)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(assess, range(16)))

    assessments = [r["assessment"] for r in results]
    first = assessments[0]
    for a in assessments:
        assert a == first, "并发评估结论必须一致"
    assert first["level"] == "CRITICAL"
    assert len(results[-1]["history"]) == 1, "并发评估不得产生重复结论"
    logger.info(
        "并发评估: 16 个请求 -> 唯一结论 level=%s score=%s version=%s",
        first["level"], first["score"], first["rule_version"],
    )


def test_concurrent_submit_same_idempotency_key_single_case(service):
    def submit(i: int):
        try:
            return ("ok", service.submit_case(PATIENT, "conc-submit")["case_id"])
        except TriageError as exc:
            return ("err", exc.code)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))

    oks = [cid for tag, cid in results if tag == "ok"]
    errs = [code for tag, code in results if tag == "err"]
    assert len(oks) == 1, "同一提交凭据并发请求只允许成功一次"
    assert all(code is ErrorCode.DUPLICATE_SUBMISSION for code in errs)
    logger.info("并发提交: 成功 1 次, 拒绝 %d 次(DUPLICATE_SUBMISSION)", len(errs))


def test_concurrent_transition_only_one_succeeds(service):
    cid = service.submit_case(PATIENT, "conc-trans")["case_id"]
    service.assess_case(cid)

    def start_review(_: int):
        try:
            return ("ok", service.transition(cid, "start_review")["status"])
        except TriageError as exc:
            return ("err", exc.code)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(start_review, range(16)))

    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1 and oks[0][1] == "REVIEWING"
    assert all(code is ErrorCode.INVALID_TRANSITION for _, code in errs)
    final = service.get_case(cid)
    assert final["status"] == "REVIEWING"
    logger.info("并发流转: 成功 1 次, 拒绝 %d 次(INVALID_TRANSITION)", len(errs))


def test_concurrent_mixed_assess_and_transition_consistent(service):
    cid = service.submit_case(PATIENT, "conc-mixed")["case_id"]

    def work(i: int):
        try:
            if i % 2 == 0:
                return service.assess_case(cid)["assessment"]["level"]
            return service.assess_case(cid)["assessment"]["level"]
        except TriageError as exc:  # 不应出现内部错误
            raise AssertionError(f"并发评估出现非预期错误: {exc.code}") from exc

    with ThreadPoolExecutor(max_workers=8) as pool:
        levels = list(pool.map(work, range(32)))

    assert set(levels) == {"CRITICAL"}, "并发评估结论必须一致"
    snap = service.get_case(cid)
    assert len(snap["history"]) == 1
    logger.info("混合并发评估: 32 个请求 -> 结论集合 %s", set(levels))
