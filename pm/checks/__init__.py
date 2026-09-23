"""체크 레지스트리: 매니페스트로부터 실행할 체크 목록을 만든다."""
from __future__ import annotations

from ..manifest import GhDeploymentCheck, GhWorkflowCheck, HttpJsonCheck, Manifest
from .base import Check, Context, Finding, Outcome
from .deps import NpmCheck, PythonDepsCheck
from .git import GitCheck
from .hygiene import HygieneCheck
from .llm_models import LlmModelsCheck
from .ops import GhDeployment, GhWorkflow, HttpJson
from .verify import Verify

__all__ = ["Check", "Context", "Finding", "Outcome", "build_checks"]


def build_checks(m: Manifest) -> list[Check]:
    checks: list[Check] = [GitCheck(), HygieneCheck()]
    if "node" in m.stack:
        checks.append(NpmCheck())
    if "python" in m.stack:
        checks.append(PythonDepsCheck())
    for h in m.health:
        if isinstance(h, GhWorkflowCheck):
            checks.append(GhWorkflow(h))
        elif isinstance(h, HttpJsonCheck):
            checks.append(HttpJson(h))
        elif isinstance(h, GhDeploymentCheck):
            checks.append(GhDeployment(h))
    checks += [Verify(name, cmd) for name, cmd in m.verify.items()]
    checks.append(LlmModelsCheck())

    # disabled_checks는 전체 ID("gh:publish_next.yml") 또는 종류("gh")로 끌 수 있다
    off = set(m.disabled_checks)
    return [c for c in checks if c.id not in off and c.id.split(":", 1)[0] not in off]
