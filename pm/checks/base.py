"""체크 공통 타입.

체크는 결정론적이어야 한다(LLM 호출 없음). 결과는 Outcome 하나로,
그 안의 Finding 목록이 '지금 이 체크가 보고 있는 문제들'이다.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from .. import paths, proc
from ..config import Config
from ..manifest import Manifest
from ..store import Store

Severity = Literal["critical", "warning", "info"]
Status = Literal["ok", "warn", "fail", "error", "skipped"]


@dataclass
class Finding:
    key: str
    severity: Severity
    title: str
    detail: str = ""
    url: str | None = None


@dataclass
class Outcome:
    status: Status
    summary: str
    value: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    @classmethod
    def of(cls, summary: str, findings: list[Finding] | None = None, value: dict | None = None):
        """Finding 심각도로 상태를 정한다: critical → fail, warning → warn, 그 외 ok."""
        findings = findings or []
        sev = {f.severity for f in findings}
        status: Status = "fail" if "critical" in sev else "warn" if "warning" in sev else "ok"
        return cls(status, summary, value or {}, findings)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Outcome":
        return cls(d["status"], d["summary"], d.get("value", {}),
                   [Finding(**f) for f in d.get("findings", [])])


class Check:
    id: str = ""
    label: str = ""
    # True면 매시간 tick마다 도는 '가벼운' 체크(운영 헬스). 나머지는 전체 스캔(6시간)에서만 돈다.
    light: bool = False

    def run(self, ctx: "Context") -> Outcome:  # pragma: no cover - 인터페이스
        raise NotImplementedError


@dataclass
class Context:
    manifest: Manifest
    config: Config
    store: Store
    http: httpx.Client
    _git: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.manifest.abs_path

    def git(self, *args: str, timeout: float = 30) -> proc.Result:
        return proc.run([proc.tool("git"), *args], cwd=self.path, timeout=timeout)

    @property
    def is_git(self) -> bool:
        if "is_git" not in self._git:
            r = self.git("rev-parse", "--show-toplevel")
            # 상위 폴더의 저장소에 딸린 하위 폴더는 독립 프로젝트로 보지 않는다.
            # git은 한글 경로를 NFD로 돌려줄 수 있어서 NFC로 맞춰 비교한다.
            self._git["is_git"] = r.ok and paths.same(Path(r.out.strip()).resolve(), self.path.resolve())
        return self._git["is_git"]

    def head(self) -> str | None:
        if "head" not in self._git:
            r = self.git("rev-parse", "HEAD")
            self._git["head"] = r.out.strip() if r.ok else None
        return self._git["head"]

    def dirty_paths(self) -> list[str]:
        if "dirty" not in self._git:
            r = self.git("status", "--porcelain=v1", "-z", "--untracked-files=normal")
            self._git["dirty"] = _parse_porcelain(r.out) if r.ok else []
        return self._git["dirty"]

    def venv_python(self) -> Path | None:
        for d in (".venv", "venv"):
            p = self.path / d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if p.exists():
                return p
        return None


def _parse_porcelain(out: str) -> list[str]:
    """`git status --porcelain -z` 출력에서 경로만 뽑는다. rename/copy는 뒤따르는 원래 경로를 건너뛴다."""
    tokens = out.split("\0")
    found: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        i += 1
        if len(t) < 4:
            continue
        xy, p = t[:2], t[3:]
        found.append(p)
        if xy[0] in "RC":
            i += 1
    return found
