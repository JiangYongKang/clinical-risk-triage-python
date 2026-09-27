"""pytest 公共夹具与测试日志：打印输入与判定依据。"""
from __future__ import annotations

import logging

import pytest

from app.api import create_app
from app.engine import RuleEngine
from app.repository import CaseRepository
from app.service import TriageService
from fastapi.testclient import TestClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("triage-test")


@pytest.fixture
def service() -> TriageService:
    return TriageService(repository=CaseRepository(), engine=RuleEngine())


@pytest.fixture
def client(service: TriageService) -> TestClient:
    return TestClient(create_app(service))


def log_assessment(title: str, patient: dict, snapshot: dict) -> None:
    """统一打印测试输入与判定依据（可追溯）。"""
    assessment = snapshot.get("assessment")
    logger.info("=== %s ===", title)
    logger.info("输入病例: %s", patient)
    if assessment:
        logger.info(
            "判定结果: 等级=%s 总分=%s 规则版本=%s",
            assessment["level"], assessment["score"], assessment["rule_version"],
        )
        for reason in assessment["reasons"]:
            logger.info("判定依据: %s", reason)
