"""对外接口：错误类型映射为可区分的 HTTP 响应。"""
import logging

from fastapi.testclient import TestClient

from app.api import create_app

log = logging.getLogger(__name__)

CASE = {"case_id": "api-1", "age": 70, "systolic_bp": 190,
        "heart_rate": 130, "temperature_c": 39.0, "spo2": 90,
        "symptoms": ["chest_pain"]}


def _client() -> TestClient:
    return TestClient(create_app())


def test_submit_and_evaluate_via_api():
    c = _client()
    r = c.post("/cases", json={"idempotency_key": "a1", "case": CASE})
    assert r.status_code == 201
    r = c.post("/cases/api-1/evaluate")
    body = r.json()
    log.info("输入: %s", CASE)
    log.info("判定依据: %s", body["rationale"])
    assert r.status_code == 200
    assert body["risk_level"] == "CRITICAL"
    assert body["rule_version"] == "v1"


def test_api_error_codes_distinguishable():
    c = _client()
    c.post("/cases", json={"idempotency_key": "a2", "case": CASE})
    r = c.post("/cases/api-1/transitions", json={"target": "CLOSED"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ILLEGAL_TRANSITION"
    r = c.post("/cases/nope/evaluate")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "CASE_NOT_FOUND"
    r = c.post("/cases/api-1/evaluate", params={"rule_version": "vX"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RULE_VERSION_NOT_FOUND"
    log.info("判定依据: 三类错误码可区分 409/404/404")
