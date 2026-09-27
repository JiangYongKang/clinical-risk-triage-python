"""FastAPI 路由：对外接口将内部错误归一化为可区分的 HTTP 响应。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .errors import (
    CaseNotFoundError,
    DuplicateSubmissionError,
    IllegalTransitionError,
    RuleConfigError,
    RuleEvaluationError,
    RuleVersionNotFoundError,
    TriageError,
)
from .models import CaseStatus, PatientCase, RuleVersion
from .rules import RuleRegistry
from .service import TriageService
from .store import CaseStore

_ERROR_STATUS = {
    DuplicateSubmissionError.code: 409,
    IllegalTransitionError.code: 409,
    CaseNotFoundError.code: 404,
    RuleVersionNotFoundError.code: 404,
    RuleConfigError.code: 422,
    RuleEvaluationError.code: 500,
}


class CaseIn(BaseModel):
    case_id: str
    age: int = Field(ge=0, le=150)
    systolic_bp: int = Field(ge=0)
    heart_rate: int = Field(ge=0)
    temperature_c: float
    spo2: float = Field(ge=0, le=100)
    symptoms: list[str] = []


class SubmitIn(BaseModel):
    idempotency_key: str
    case: CaseIn


class TransitionIn(BaseModel):
    target: CaseStatus


def _default_rules() -> RuleRegistry:
    reg = RuleRegistry()
    reg.register(
        RuleVersion(
            version="v1",
            thresholds={"age": 65, "systolic_bp": 180, "heart_rate": 120,
                        "temperature_c": 38.5, "spo2": 92},
            symptom_weights={"chest_pain": 3.0, "dyspnea": 2.0,
                             "syncope": 2.5},
            level_cutoffs={"MEDIUM": 2.0, "HIGH": 5.0, "CRITICAL": 8.0},
        )
    )
    return reg


def create_app(service: TriageService | None = None) -> FastAPI:
    app = FastAPI(title="clinical-risk-triage")
    if service is None:
        service = TriageService(CaseStore(), _default_rules())
    app.state.service = service

    @app.exception_handler(TriageError)
    async def triage_error_handler(_: Request, exc: TriageError) -> JSONResponse:
        status = _ERROR_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.post("/cases", status_code=201)
    def submit_case(body: SubmitIn) -> dict:
        case = PatientCase(
            case_id=body.case.case_id,
            age=body.case.age,
            systolic_bp=body.case.systolic_bp,
            heart_rate=body.case.heart_rate,
            temperature_c=body.case.temperature_c,
            spo2=body.case.spo2,
            symptoms=tuple(body.case.symptoms),
        )
        saved = app.state.service.submit_case(body.idempotency_key, case)
        return {"case_id": saved.case_id, "status": "SUBMITTED"}

    @app.post("/cases/{case_id}/evaluate")
    def evaluate_case(case_id: str, rule_version: str | None = None) -> dict:
        a = app.state.service.evaluate_case(case_id, rule_version)
        return {
            "case_id": a.case_id,
            "rule_version": a.rule_version,
            "risk_level": a.risk_level.value,
            "score": a.score,
            "rationale": list(a.rationale),
        }

    @app.post("/cases/{case_id}/transitions")
    def transition_case(case_id: str, body: TransitionIn) -> dict:
        status = app.state.service.transition_case(case_id, body.target)
        return {"case_id": case_id, "status": status.value}

    @app.get("/cases/{case_id}")
    def get_case(case_id: str) -> dict:
        svc = app.state.service
        return {"case_id": case_id, "status": svc._store.get_status(case_id).value}

    return app
