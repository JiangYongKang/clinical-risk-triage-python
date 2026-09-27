"""HTTP 入口测试：状态码、错误结构、幂等与并发一致性。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from tests.conftest import logger

PATIENT = {"age": 70, "spo2": 90}


def _submit(client, key: str, patient=None):
    return client.post("/cases", json={
        "patient": patient or PATIENT, "idempotency_key": key
    })


def test_submit_and_duplicate(client):
    r1 = _submit(client, "api-1")
    assert r1.status_code == 201
    cid = r1.json()["case_id"]

    r2 = _submit(client, "api-1")
    assert r2.status_code == 409
    err = r2.json()["error"]
    assert err["code"] == "DUPLICATE_SUBMISSION"
    assert cid in err["message"]
    logger.info("HTTP 重复提交: %s %s", r2.status_code, err["code"])


def test_illegal_transition_distinguishable(client):
    cid = _submit(client, "api-2").json()["case_id"]

    r = client.post(f"/cases/{cid}/transitions", json={"action": "start_review"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CASE_NOT_TRIAGED"

    r = client.post(f"/cases/{cid}/transitions", json={"action": "confirm"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_TRANSITION"

    r = client.post(f"/cases/{cid}/transitions", json={"action": "nope"})
    assert r.status_code == 409
    assert "未知流转动作" in r.json()["error"]["message"]
    logger.info("HTTP 非法流转原因可区分")


def test_assess_and_version_switch_via_http(client):
    patient = {"age": 70, "spo2": 90}  # v1 MEDIUM(5), v2 HIGH(6)
    cid = _submit(client, "api-3", patient).json()["case_id"]

    r = client.post(f"/cases/{cid}/assess", json={"rule_version": "v2.0"})
    assert r.status_code == 200
    assert r.json()["assessment"]["level"] == "HIGH"

    # 评估入口换版本被拒绝
    r = client.post(f"/cases/{cid}/assess", json={"rule_version": "v1.0"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_TRANSITION"

    # recompute 复现旧版本，不覆盖绑定结论
    r = client.post(f"/cases/{cid}/recompute", json={"rule_version": "v1.0"})
    assert r.status_code == 200
    body = r.json()
    assert body["recomputed"]["level"] == "MEDIUM"
    assert body["bound_assessment"]["rule_version"] == "v2.0"
    assert body["rewrites_history"] is False

    r = client.post(f"/cases/{cid}/recompute", json={"rule_version": "vX"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RULE_VERSION_NOT_FOUND"
    logger.info("HTTP 版本切换/重算符合预期")


def test_case_not_found_and_bad_payload(client):
    assert client.get("/cases/ghost").status_code == 404
    r = client.post("/cases", json={"patient": {"age": "x"}, "idempotency_key": "k"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_PAYLOAD"


def test_concurrent_assess_via_http_single_result(client):
    cid = _submit(client, "api-conc", {"age": 81, "spo2": 90}).json()["case_id"]

    def assess(_):
        return client.post(f"/cases/{cid}/assess", json={}).json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(assess, range(16)))

    levels = {r["assessment"]["level"] for r in results}
    versions = {r["assessment"]["rule_version"] for r in results}
    assert levels == {"CRITICAL"}, levels
    assert versions == {"v2.0"}
    assert len(results[-1]["history"]) == 1
    logger.info("HTTP 并发评估结论一致: %s %s", levels, versions)


def test_full_review_flow_and_audit_trail(client):
    cid = _submit(client, "api-flow", PATIENT).json()["case_id"]
    client.post(f"/cases/{cid}/assess", json={})
    assert client.post(f"/cases/{cid}/transitions",
                       json={"action": "start_review"}).json()["status"] == "REVIEWING"
    assert client.post(f"/cases/{cid}/transitions",
                       json={"action": "confirm"}).json()["status"] == "CONFIRMED"

    case = client.get(f"/cases/{cid}").json()
    actions = [e["action"] for e in case["audit"]]
    assert actions == ["SUBMIT", "ASSESS", "START_REVIEW", "CONFIRM"]
    assess_event = next(e for e in case["audit"] if e["action"] == "ASSESS")
    assert assess_event["rule_version"] == "v2.0"
    logger.info("审核轨迹: %s", actions)
