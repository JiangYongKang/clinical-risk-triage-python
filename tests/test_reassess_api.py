"""重算/轨迹接口与坏输入的统一错误结构。"""
import logging

from fastapi.testclient import TestClient

from app.api import create_app

log = logging.getLogger(__name__)

CASE = {"case_id": "api-r", "age": 70, "systolic_bp": 190,
        "heart_rate": 130, "temperature_c": 39.0, "spo2": 90,
        "symptoms": ["chest_pain"]}


def _client() -> TestClient:
    c = TestClient(create_app())
    c.post("/cases", json={"idempotency_key": "r1", "case": CASE})
    c.post("/cases/api-r/evaluate", params={"rule_version": "v1"})
    return c


def test_reassess_endpoint_reproduces():
    c = _client()
    first = c.post("/cases/api-r/reassess", params={"rule_version": "v1"})
    assert first.status_code == 200
    body = first.json()
    log.info("输入: 按 v1 重算病例 api-r")
    log.info("判定依据: %s", body["rationale"])
    assert body["rule_version"] == "v1"
    assert body["risk_level"] == "CRITICAL"
    # 再算一次，结论完全一致
    again = c.post("/cases/api-r/reassess", params={"rule_version": "v1"}).json()
    assert {k: again[k] for k in ("rule_version", "risk_level", "score",
                                  "rationale")} == \
           {k: body[k] for k in ("rule_version", "risk_level", "score",
                                 "rationale")}


def test_reassess_version_mismatch_via_api():
    c = _client()
    # 默认规则表只有 v1；未登记版本与绑定不一致分别可区分
    r = c.post("/cases/api-r/reassess", params={"rule_version": "vX"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RULE_VERSION_NOT_FOUND"
    log.info("判定依据: 未登记版本 -> 404 RULE_VERSION_NOT_FOUND")


def test_trail_endpoint_ordered_and_immutable():
    c = _client()
    c.post("/cases/api-r/reassess", params={"rule_version": "v1"})
    r = c.get("/cases/api-r/assessments")
    assert r.status_code == 200
    records = r.json()["records"]
    log.info("判定依据: 轨迹=%s",
             [(x["kind"], x["rule_version"], x["recorded_at"]) for x in records])
    assert [x["kind"] for x in records] == ["EVALUATE", "REASSESS"]
    assert all(x["rule_version"] == "v1" for x in records)
    assert all(x["recorded_at"] for x in records)
    # 重复重算不冲掉、不复制历史记录
    c.post("/cases/api-r/reassess", params={"rule_version": "v1"})
    again = c.get("/cases/api-r/assessments").json()["records"]
    assert again == records


def test_invalid_input_unified_error_structure():
    c = _client()
    # 字段缺失
    r = c.post("/cases", json={"idempotency_key": "bad1",
                               "case": {"case_id": "x"}})
    assert r.status_code == 422
    err = r.json()["error"]
    log.info("输入: 缺字段请求体; 判定依据: %s", err)
    assert err["code"] == "INVALID_REQUEST"
    assert "detail" not in r.json()  # 不透出框架原始校验细节
    # 类型写错
    r = c.post("/cases", json={"idempotency_key": "bad2",
                               "case": {**CASE, "case_id": "y",
                                        "age": "not-a-number"}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    # 流转目标类型非法
    r = c.post("/cases/api-r/transitions", json={"target": "NOWHERE"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    # reassess 缺少必填版本参数
    r = c.post("/cases/api-r/reassess")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    log.info("判定依据: 四类坏输入统一为 422 INVALID_REQUEST")
