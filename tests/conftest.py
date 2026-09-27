import logging

import pytest

from app.models import PatientCase, RuleVersion
from app.rules import RuleRegistry
from app.service import TriageService
from app.store import CaseStore

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)


def make_rule(version: str, age_threshold: float = 65) -> RuleVersion:
    return RuleVersion(
        version=version,
        thresholds={"age": age_threshold, "systolic_bp": 180,
                    "heart_rate": 120, "temperature_c": 38.5, "spo2": 92},
        symptom_weights={"chest_pain": 3.0, "dyspnea": 2.0},
        level_cutoffs={"MEDIUM": 2.0, "HIGH": 5.0, "CRITICAL": 8.0},
    )


@pytest.fixture()
def rules() -> RuleRegistry:
    reg = RuleRegistry()
    reg.register(make_rule("v1"))
    return reg


@pytest.fixture()
def service(rules: RuleRegistry) -> TriageService:
    return TriageService(CaseStore(), rules)


@pytest.fixture()
def sample_case() -> PatientCase:
    return PatientCase(
        case_id="case-1", age=70, systolic_bp=190, heart_rate=130,
        temperature_c=39.0, spo2=90.0, symptoms=("chest_pain",),
    )
