"""FastAPI 入口：HTTP 层负责参数解析与异常归一化。

业务异常统一输出 {"error": {"code", "message"}}，
内部错误细节不透出；服务单例保证内存数据与病例锁全局一致。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.errors import ErrorCode, TriageError
from app.rules import list_versions
from app.service import TriageService

_HTTP_STATUS = {
    ErrorCode.CASE_NOT_FOUND: 404,
    ErrorCode.RULE_VERSION_NOT_FOUND: 404,
    ErrorCode.DUPLICATE_SUBMISSION: 409,
    ErrorCode.INVALID_TRANSITION: 409,
    ErrorCode.CASE_NOT_TRIAGED: 409,
    ErrorCode.INVALID_PAYLOAD: 422,
    ErrorCode.RULE_CONFIG_ERROR: 500,
    ErrorCode.ENGINE_INTERNAL_ERROR: 500,
}


class SubmitRequest(BaseModel):
    patient: dict
    idempotency_key: str


class AssessRequest(BaseModel):
    rule_version: str | None = None


class RecomputeRequest(BaseModel):
    rule_version: str


class TransitionRequest(BaseModel):
    action: str


def create_app(service: TriageService | None = None) -> FastAPI:
    """构建 FastAPI 应用，可注入服务（便于测试）。"""
    app = FastAPI(title="本地临床风险分层与决策支持服务", version="0.1.0")
    app.state.service = service or TriageService()

    @app.exception_handler(TriageError)
    async def _triage_error_handler(_: Request, exc: TriageError) -> JSONResponse:
        return JSONResponse(
            status_code=_HTTP_STATUS.get(exc.code, 500),
            content={"error": {"code": exc.code.value, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": ErrorCode.INVALID_PAYLOAD.value,
                    "message": "请求参数不合法",
                    "details": exc.errors(),
                }
            },
        )

    @app.get("/")
    def root() -> dict:
        return {"service": "clinical-risk-triage", "rule_versions": list_versions()}

    @app.get("/rules/versions")
    def get_versions() -> dict:
        return {"versions": list_versions()}

    @app.post("/cases", status_code=201)
    def submit_case(req: SubmitRequest) -> dict:
        return app.state.service.submit_case(req.patient, req.idempotency_key)

    @app.get("/cases/{case_id}")
    def get_case(case_id: str) -> dict:
        return app.state.service.get_case(case_id)

    @app.post("/cases/{case_id}/assess")
    def assess_case(case_id: str, req: AssessRequest) -> dict:
        return app.state.service.assess_case(case_id, req.rule_version)

    @app.post("/cases/{case_id}/recompute")
    def recompute(case_id: str, req: RecomputeRequest) -> dict:
        return app.state.service.recompute(case_id, req.rule_version)

    @app.post("/cases/{case_id}/transitions")
    def transition(case_id: str, req: TransitionRequest) -> dict:
        return app.state.service.transition(case_id, req.action)

    return app
