"""프로젝트 매니페스트: 관리 대상 프로젝트 하나의 목표·정책·체크 정의.

에이전트가 매번 프로젝트를 추측하지 않도록, 사람이 확인한 정보를 여기에 둔다.
파일은 앱 데이터 폴더의 projects/<id>.yaml 이다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import paths
from .config import Autonomy

Stage = Literal["idea", "prototype", "mvp", "deployed", "users", "revenue"]
STAGES: tuple[str, ...] = ("idea", "prototype", "mvp", "deployed", "users", "revenue")
Severity = Literal["critical", "warning", "info"]

_HEADER = """\
# pm-agent 프로젝트 매니페스트 — 자동 생성 후 사람이 확인·수정한다.
# stage:    idea | prototype | mvp | deployed | users | revenue
# autonomy: L0 관찰 / L1 제안 / L2 PR까지 / L3 저위험 자동 머지
#           (L3는 테스트+헬스체크(V2)가 갖춰진 경우에만 실제로 적용되고, 아니면 L2로 제한된다)
# verify:   검증 명령. {venv_python}은 프로젝트 .venv의 파이썬으로 바뀐다.
# health:   운영 헬스체크 (gh_workflow | http_json | gh_deployment).
#           값에 ${keychain:NAME}을 쓰면 키체인에서 읽는다.
"""


class GhWorkflowCheck(BaseModel):
    type: Literal["gh_workflow"] = "gh_workflow"
    workflow: str
    window: int = 10
    max_failure_rate: float = 0.3
    max_age_hours: float | None = None


class HttpJsonCheck(BaseModel):
    type: Literal["http_json"] = "http_json"
    name: str
    url: str
    field: str | None = None
    # "HH:MM" — 이 시각(tz 기준)이 지나면 field의 날짜가 오늘이어야 한다
    daily_due: str | None = None
    tz: str = "Asia/Seoul"
    max_age_hours: float | None = None
    expect_status: int = 200


class GhDeploymentCheck(BaseModel):
    """GitHub Deployments 기록(Vercel·Netlify 등 GitHub 연동 배포가 남김)으로 배포 성공 여부를 본다."""
    type: Literal["gh_deployment"] = "gh_deployment"
    environment: str = "Production"
    window: int = 5


HealthCheck = Annotated[
    Union[GhWorkflowCheck, HttpJsonCheck, GhDeploymentCheck], Field(discriminator="type")
]


class VerifyCommand(BaseModel):
    run: str
    timeout_sec: int = 600
    severity: Severity = "critical"


class Budget(BaseModel):
    monthly_usd: float = 10
    per_task_usd: float = 2


class Manifest(BaseModel):
    id: str
    name: str
    path: str
    repo: str | None = None
    stack: list[str] = []
    stage: Stage = "mvp"
    autonomy: Autonomy = "L2"
    goal: str = ""
    revenue_model: str = "none"
    budget: Budget = Budget()
    verify: dict[str, VerifyCommand] = {}
    health: list[HealthCheck] = []
    protected_paths: list[str] = []
    exclude: list[str] = []
    disabled_checks: list[str] = []
    notes: str = ""

    @field_validator("verify", mode="before")
    @classmethod
    def _coerce_verify(cls, v):
        # `test: "pytest -q"`처럼 명령 문자열만 적어도 되게 한다
        if isinstance(v, dict):
            return {k: ({"run": c} if isinstance(c, str) else c) for k, c in v.items()}
        return v

    @property
    def abs_path(self) -> Path:
        return paths.resolve(self.path)

    @property
    def verification_level(self) -> str:
        """V2 = 테스트 + 운영 헬스체크, V1 = 검증 수단 일부, V0 = 없음."""
        has_test = "test" in self.verify
        has_health = bool(self.health)
        if has_test and has_health:
            return "V2"
        if self.verify or has_health:
            return "V1"
        return "V0"

    @property
    def effective_autonomy(self) -> str:
        """L3(자동 머지)는 V2 검증이 있을 때만 허용하고, 모자라면 L2로 묶는다."""
        if self.autonomy == "L3" and self.verification_level != "V2":
            return "L2"
        return self.autonomy


def manifest_file(pid: str) -> Path:
    return paths.projects_dir() / f"{pid}.yaml"


def load(pid: str) -> Manifest:
    data = yaml.safe_load(manifest_file(pid).read_text(encoding="utf-8")) or {}
    return Manifest.model_validate(data)


def load_all() -> tuple[list[Manifest], list[str]]:
    """(매니페스트 목록, 읽지 못한 파일의 오류 메시지 목록)."""
    d = paths.projects_dir()
    out: list[Manifest] = []
    errors: list[str] = []
    if not d.exists():
        return out, errors
    for f in sorted(d.glob("*.yaml")):
        try:
            out.append(Manifest.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")) or {}))
        except (ValidationError, yaml.YAMLError) as e:
            errors.append(f"{f.name}: {e}")
    out.sort(key=lambda m: m.name.lower())
    return out, errors


def save(m: Manifest) -> Path:
    f = manifest_file(m.id)
    f.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        m.model_dump(mode="json", exclude_none=True), allow_unicode=True, sort_keys=False
    )
    f.write_text(_HEADER + body, encoding="utf-8")
    return f
