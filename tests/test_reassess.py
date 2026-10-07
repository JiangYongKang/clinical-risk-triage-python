"""指定版本重算、评估轨迹、统一输入报错与混合版本并发。"""
import logging
import threading

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.errors import RuleVersionNotFoundError
from app.models import AssessmentKind, CaseStatus, RiskLevel
from app.service import TriageService
from app.store import CaseStore
from tests.conftest import make_rule

log = logging.getLogger(__name__)

CASE = {"case_id": "rs-1", "age": 70, "systolic_bp": 190,
        "heart_rate": 130, "temperature_c": 39.0, "spo2": 90,
        "symptoms": ["chest_pain"]}


def _client_with_v2() -> TestClient:
    from app.rules import RuleRegistry
    reg = RuleRegistry()
    reg.register(make_rule("v1"))
    reg.register(make_rule("v2", age_threshold=200))
    return TestClient(create_app(TriageService(CaseStore(), reg)))


def test_reassess_reproduces_bound_result(service, sample_case):
    service.submit_case("r-1", sample_case)
    bound = service.evaluate_case(sample_case.case_id, rule_version="v1")
    again = service.reassess_case(sample_case.case_id, "v1")
    log.info("输入: 病例 %s 按绑定版本 v1 重算", sample_case.case_id)
    log.info("判定依据: %s", list(again.rationale))
    assert again.rule_version == bound.rule_version == "v1"
    assert again.score == bound.score
    assert again.risk_level == bound.risk_level
    assert again.rationale == bound.rationale
    # 只读：绑定结论不被重算覆盖
    assert service.get_assessment(sample_case.case_id) == bound


def test_reassess_version_mismatch_distinguishable(service, rules, sample_case):
    service.submit_case("r-2", sample_case)
    bound = service.evaluate_case(sample_case.case_id, rule_version="v1")
    rules.register(make_rule("v2", age_threshold=200))
    other = service.reassess_case(sample_case.case_id, "v2")
    log.info("输入: 绑定 v1, 用 v2 重算 -> %s %.1f", other.risk_level, other.score)
    # 版本不一致：返回带明确版本标识的重算结论，而非报错或静默忽略
    assert other.rule_version == "v2"
    assert other.rule_version != bound.rule_version
    assert other.score == bound.score - 2.0
    # 未注册版本：可区分的另一种处理结果
    with pytest.raises(RuleVersionNotFoundError) as exc:
        service.reassess_case(sample_case.case_id, "vX")
    assert exc.value.code == "RULE_VERSION_NOT_FOUND"
    log.info("判定依据: 不一致->返回 v2 结论并标注; 未注册->%s", exc.value.code)
    # 两种情况下绑定结论均不被覆盖
    assert service.get_assessment(sample_case.case_id) == bound


def test_history_ordered_and_not_rewritten(service, rules, sample_case):
    service.submit_case("r-3", sample_case)
    bound = service.evaluate_case(sample_case.case_id, rule_version="v1")
    rules.register(make_rule("v2", age_threshold=200))
    service.reassess_case(sample_case.case_id, "v2")
    service.reassess_case(sample_case.case_id, "v1")

    history = service.get_history(sample_case.case_id)
    for rec in history:
        log.info("轨迹: seq=%d kind=%s 版本=%s 时间=%s 分数=%.1f 等级=%s",
                 rec.seq, rec.kind.value, rec.assessment.rule_version,
                 rec.assessment.evaluated_at.isoformat(),
                 rec.assessment.score, rec.assessment.risk_level.value)
    assert [r.seq for r in history] == [1, 2, 3]
    assert [r.kind for r in history] == [
        AssessmentKind.EVALUATE, AssessmentKind.REASSESS, AssessmentKind.REASSESS
    ]
    assert [r.assessment.rule_version for r in history] == ["v1", "v2", "v1"]
    assert all(r.assessment.evaluated_at is not None for r in history)
    # 首条记录仍是当初的 v1 结论，不被后续新版本重算追溯改写
    first = history[0].assessment
    assert first == bound
    assert first.rule_version == "v1"
    assert first.risk_level == RiskLevel.CRITICAL


def test_concurrent_mixed_version_reassess(service, rules, sample_case):
    service.submit_case("r-4", sample_case)
    bound = service.evaluate_case(sample_case.case_id, rule_version="v1")
    rules.register(make_rule("v2", age_threshold=200))
    expected = {
        v: (a.risk_level, a.score, a.rationale)
        for v in ("v1", "v2")
        for a in [rules.evaluate(sample_case, v)]
    }
    results, errors = [], []

    def work(version: str):
        try:
            results.append(service.reassess_case(sample_case.case_id, version))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=work, args=(f"v{i % 2 + 1}",))
        for i in range(32)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发重算不应出错: {errors}"
    assert len(results) == 32
    by_version: dict[str, set] = {}
    for r in results:
        by_version.setdefault(r.rule_version, set()).add(
            (r.risk_level, r.score, r.rationale)
        )
    log.info("输入: 32 线程混合 v1/v2 并发重算病例 %s", sample_case.case_id)
    for version, distinct in by_version.items():
        log.info("判定依据: 版本 %s 去重后 %d 种结论", version, len(distinct))
    # 每个版本结论各自自洽唯一，且与纯函数重算一致
    assert set(by_version) == {"v1", "v2"}
    for version, distinct in by_version.items():
        assert len(distinct) == 1
        assert next(iter(distinct)) == expected[version]
    # 绑定结论不被并发重算覆盖；轨迹完整：1 次评估 + 32 次重算
    assert service.get_assessment(sample_case.case_id) == bound
    history = service.get_history(sample_case.case_id)
    assert len(history) == 33
    assert [r.seq for r in history] == list(range(1, 34))


def test_api_reassess_and_history():
    c = _client_with_v2()
    c.post("/cases", json={"idempotency_key": "ra1", "case": CASE})
    r = c.post("/cases/rs-1/evaluate", params={"rule_version": "v1"})
    assert r.status_code == 200

    r = c.post("/cases/rs-1/reassess", json={"rule_version": "v1"})
    body = r.json()
    log.info("输入: POST reassess v1; 判定依据: %s", body["rationale"])
    assert r.status_code == 200
    assert body["version_match"] is True
    assert body["risk_level"] == "CRITICAL"

    r = c.post("/cases/rs-1/reassess", json={"rule_version": "v2"})
    body = r.json()
    log.info("输入: POST reassess v2; 判定依据: %s", body["rationale"])
    assert r.status_code == 200
    assert body["version_match"] is False
    assert body["bound_rule_version"] == "v1"
    assert body["rule_version"] == "v2"

    r = c.post("/cases/rs-1/reassess", json={"rule_version": "vX"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RULE_VERSION_NOT_FOUND"

    r = c.get("/cases/rs-1/assessments")
    records = r.json()["records"]
    log.info("轨迹: %s", [(x["seq"], x["kind"], x["rule_version"])
                        for x in records])
    assert [x["seq"] for x in records] == [1, 2, 3]
    assert [x["kind"] for x in records] == ["EVALUATE", "REASSESS", "REASSESS"]
    assert [x["rule_version"] for x in records] == ["v1", "v1", "v2"]
    assert all(x["evaluated_at"] for x in records)


def test_api_invalid_request_unified_error():
    c = _client_with_v2()
    c.post("/cases", json={"idempotency_key": "ra2", "case": CASE})
    c.post("/cases/rs-1/evaluate")

    # 请求体字段缺失
    r = c.post("/cases/rs-1/reassess", json={})
    assert r.status_code == 422
    err = r.json()["error"]
    log.info("输入: 缺失 rule_version; 判定依据: %s %s", err["code"], err["message"])
    assert err["code"] == "INVALID_REQUEST"
    assert set(err) == {"code", "message"}  # 不透出框架原始校验细节

    # 字段类型写错
    r = c.post("/cases/rs-1/reassess", json={"rule_version": 123})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"

    # 提交病例时类型写错同样走统一结构
    bad = dict(CASE, age="seventy")
    r = c.post("/cases", json={"idempotency_key": "ra3", "case": bad})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    log.info("判定依据: 三类坏输入均归一化为 INVALID_REQUEST")


def test_reassess_does_not_change_status(service, sample_case):
    service.submit_case("r-5", sample_case)
    service.evaluate_case(sample_case.case_id, rule_version="v1")
    service.transition_case(sample_case.case_id, CaseStatus.REVIEWED)
    service.reassess_case(sample_case.case_id, "v1")
    assert service._store.get_status(sample_case.case_id) == CaseStatus.REVIEWED
    log.info("判定依据: 重算为只读，不改变病例状态 REVIEWED")
