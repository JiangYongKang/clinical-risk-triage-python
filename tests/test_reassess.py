"""指定版本只读重算、评估轨迹与混合版本并发重算。"""
import logging
import threading

import pytest

from app.errors import RuleVersionMismatchError, RuleVersionNotFoundError
from app.models import AssessmentKind, CaseStatus
from tests.conftest import make_rule

log = logging.getLogger(__name__)


def _bound_case(service, sample_case):
    service.submit_case("k-r", sample_case)
    return service.evaluate_case(sample_case.case_id, rule_version="v1")


def test_reassess_reproduces_bound_assessment(service, sample_case):
    bound = _bound_case(service, sample_case)
    log.info("输入: 病例 %s 绑定版本 %s, 按 v1 重算", sample_case.case_id,
             bound.rule_version)
    again = service.reassess_case(sample_case.case_id, "v1")
    log.info("判定依据: 首次=%s 重算=%s", list(bound.rationale),
             list(again.rationale))
    assert again.rule_version == bound.rule_version == "v1"
    assert again.risk_level == bound.risk_level
    assert again.score == bound.score
    assert again.rationale == bound.rationale


def test_reassess_is_readonly_and_does_not_overwrite(service, rules,
                                                     sample_case):
    bound = _bound_case(service, sample_case)
    rules.register(make_rule("v2", age_threshold=200))  # 新版本上线
    service.reassess_case(sample_case.case_id, "v1")
    current = service._store.get_assessment(sample_case.case_id)
    log.info("判定依据: 重算后绑定结论仍为 %s/%s，未被覆盖",
             current.rule_version, current.risk_level)
    assert current == bound
    assert service._store.get_status(sample_case.case_id) == CaseStatus.EVALUATED


def test_reassess_version_mismatch(service, rules, sample_case):
    _bound_case(service, sample_case)
    rules.register(make_rule("v2", age_threshold=200))
    log.info("输入: 病例绑定 v1, 请求按 v2 重算")
    with pytest.raises(RuleVersionMismatchError) as exc:
        service.reassess_case(sample_case.case_id, "v2")
    assert exc.value.code == "RULE_VERSION_MISMATCH"
    assert exc.value.bound == "v1" and exc.value.requested == "v2"
    log.info("判定依据: 绑定 v1 != 请求 v2 -> %s", exc.value)


def test_reassess_unknown_version(service, sample_case):
    _bound_case(service, sample_case)
    with pytest.raises(RuleVersionNotFoundError) as exc:
        service.reassess_case(sample_case.case_id, "vX")
    assert exc.value.code == "RULE_VERSION_NOT_FOUND"
    log.info("判定依据: 版本 vX 未登记 -> %s", exc.value.code)


def test_trail_records_in_order(service, sample_case):
    _bound_case(service, sample_case)
    service.reassess_case(sample_case.case_id, "v1")
    trail = service.get_trail(sample_case.case_id)
    log.info("判定依据: 轨迹=%s",
             [(r.kind.value, r.rule_version) for r in trail])
    assert [r.kind for r in trail] == [
        AssessmentKind.EVALUATE, AssessmentKind.REASSESS]
    assert all(r.rule_version == "v1" for r in trail)
    assert trail[0].recorded_at <= trail[1].recorded_at
    # 任意一条记录都能说清版本与时间
    for r in trail:
        assert r.rule_version and r.recorded_at is not None


def test_trail_not_rewritten_by_new_rule_version(service, rules, sample_case):
    _bound_case(service, sample_case)
    before = service.get_trail(sample_case.case_id)
    rules.register(make_rule("v2", age_threshold=200))
    rules.register(make_rule("v3", age_threshold=300))
    after = service.get_trail(sample_case.case_id)
    log.info("判定依据: 新版本上线后轨迹不变 %s",
             [(r.kind.value, r.rule_version, r.score) for r in after])
    assert after == before


def test_concurrent_mixed_version_reassess(service, rules, sample_case):
    bound = _bound_case(service, sample_case)
    rules.register(make_rule("v2", age_threshold=200))
    results, mismatches, not_found = [], [], []

    def work(version):
        try:
            results.append(service.reassess_case(sample_case.case_id, version))
        except RuleVersionMismatchError as exc:
            mismatches.append(exc)
        except RuleVersionNotFoundError as exc:
            not_found.append(exc)

    versions = ["v1"] * 16 + ["v2"] * 8 + ["vX"] * 8
    threads = [threading.Thread(target=work, args=(v,)) for v in versions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log.info("输入: 32 线程混合版本重算 v1x16/v2x8/vXx8")
    log.info("判定依据: 成功 %d, 版本不一致 %d, 版本未登记 %d",
             len(results), len(mismatches), len(not_found))
    assert len(results) == 16 and len(mismatches) == 8 and len(not_found) == 8
    # 同一版本重复算不会冒出两种结果
    distinct = {(r.rule_version, r.risk_level, r.score, r.rationale)
                for r in results}
    assert len(distinct) == 1
    # 绑定结论不被覆盖
    assert service._store.get_assessment(sample_case.case_id) == bound
    # 轨迹不重复：EVALUATE/v1 与 REASSESS/v1 各一条，失败的重算不留痕
    trail = service.get_trail(sample_case.case_id)
    keys = [(r.kind, r.rule_version) for r in trail]
    assert keys == [(AssessmentKind.EVALUATE, "v1"),
                    (AssessmentKind.REASSESS, "v1")]
