"""并发评估：同一病例并发请求不得产生冲突或重复结论。"""
import logging
import threading

from app.models import CaseStatus

log = logging.getLogger(__name__)


def test_concurrent_evaluation_single_consistent_result(service, sample_case):
    service.submit_case("k-c", sample_case)
    results, errors = [], []

    def work():
        try:
            results.append(service.evaluate_case(sample_case.case_id))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发评估不应出错: {errors}"
    assert len(results) == 32
    distinct = {(r.rule_version, r.risk_level, r.score, r.rationale)
                for r in results}
    log.info("输入: 32 线程并发评估病例 %s", sample_case.case_id)
    log.info("判定依据: 结论=%s 分数=%.1f 版本=%s, 去重后 %d 种",
             results[0].risk_level, results[0].score,
             results[0].rule_version, len(distinct))
    assert len(distinct) == 1, "并发结论必须一致"
    assert service._store.get_status(sample_case.case_id) == CaseStatus.EVALUATED


def test_concurrent_duplicate_submission(service, sample_case):
    outcomes, errors = [], []

    def work():
        try:
            outcomes.append(service.submit_case("k-dup", sample_case))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert all(o == sample_case for o in outcomes)
    log.info("判定依据: 16 线程同 key 提交 -> 全部幂等返回同一病例，无重复")
