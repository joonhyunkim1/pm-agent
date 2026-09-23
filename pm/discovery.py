"""프로젝트 폴더를 보고 매니페스트 초안을 만든다(LLM 없이 규칙 기반).

감지하는 것: git 원격(owner/repo), 스택(node/python/flutter/docker), 테스트·빌드 명령,
예약(cron) 워크플로우 → 헬스체크, 단계(stage) 추정, 보호 경로·제외 경로.
사람이 확인·수정하는 것을 전제로 한 '초안'이다.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from . import paths, proc
from .config import Config
from .manifest import GhDeploymentCheck, GhWorkflowCheck, Manifest

_GH_REMOTE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
_NPM_DEFAULT_TEST = "no test specified"


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", paths.nfc(name).lower()).strip("-")
    return s or "p-" + hashlib.sha1(name.encode()).hexdigest()[:8]


def git_repo(path: Path) -> str | None:
    r = proc.run([proc.tool("git"), "remote", "get-url", "origin"], cwd=path, timeout=10)
    if not r.ok:
        return None
    m = _GH_REMOTE.search(r.out.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def is_git_root(path: Path) -> bool:
    r = proc.run([proc.tool("git"), "rev-parse", "--show-toplevel"], cwd=path, timeout=10)
    return r.ok and paths.same(Path(r.out.strip()).resolve(), path.resolve())


def cron_period_hours(expr: str) -> float | None:
    """cron 표현식의 대략적인 실행 간격(시간). 실행 사이 가장 긴 간격을 돌려준다."""
    parts = expr.split()
    if len(parts) != 5:
        return None
    minute, hour, dom, _mon, dow = parts

    def values(field: str, lo: int, hi: int) -> list[int] | None:
        if field == "*":
            return list(range(lo, hi + 1))
        if field.startswith("*/"):
            return list(range(lo, hi + 1, int(field[2:])))
        out: list[int] = []
        for item in field.split(","):
            if "-" in item:
                a, b = item.split("-", 1)
                out += list(range(int(a), int(b) + 1))
            elif item.isdigit():
                out.append(int(item))
            else:
                return None
        return sorted(set(out))

    def max_gap(vals: list[int], cycle: int) -> int:
        if len(vals) == 1:
            return cycle
        return max((vals[(i + 1) % len(vals)] - v) % cycle or cycle for i, v in enumerate(vals))

    if dom != "*":
        return 24 * 31.0
    if dow != "*":
        days = values(dow, 0, 6)
        return 24.0 * max_gap(days, 7) if days else None
    hours = values(hour, 0, 23)
    if hours is None:
        return None
    if len(hours) == 24:
        mins = values(minute, 0, 59)
        return max_gap(mins, 60) / 60 if mins else 1.0
    return float(max_gap(hours, 24))


def scheduled_workflows(path: Path) -> list[GhWorkflowCheck]:
    out = []
    wf_dir = path / ".github" / "workflows"
    for f in sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        # PyYAML은 'on:' 키를 불리언 True로 읽는다
        on = data.get("on", data.get(True)) or {}
        sched = on.get("schedule") if isinstance(on, dict) else None
        if not sched:
            continue
        periods = [p for s in sched if (p := cron_period_hours(str(s.get("cron", ""))))]
        max_age = round(min(periods) * 1.5 + 3, 1) if periods else None
        out.append(GhWorkflowCheck(workflow=f.name, max_age_hours=max_age))
    return out


def deployment_checks(repo: str) -> list[GhDeploymentCheck]:
    """GitHub Deployments 기록이 있는 레포(Vercel 등 연동)에 배포 상태 체크를 붙인다."""
    try:
        r = proc.run([proc.tool("gh"), "api", f"repos/{repo}/deployments?per_page=20",
                      "--jq", "[.[].environment] | unique"], timeout=20)
    except proc.ToolMissing:
        return []
    if not r.ok:
        return []
    envs: list[str] = json.loads(r.out or "[]")
    if not envs:
        return []
    env = next((e for e in envs if e.lower() == "production"), envs[0])
    return [GhDeploymentCheck(environment=env)]


def detect_stack(path: Path) -> list[str]:
    stack = []
    if (path / "package.json").exists():
        stack.append("node")
    if ((path / "pyproject.toml").exists() or list(path.glob("requirements*.txt"))
            or list(path.glob("*/requirements.txt"))):
        stack.append("python")
    if (path / "pubspec.yaml").exists() or list(path.glob("*/pubspec.yaml")):
        stack.append("flutter")
    if (path / "docker-compose.yml").exists() or (path / "Dockerfile").exists():
        stack.append("docker")
    return stack


def detect_verify(path: Path, stack: list[str]) -> dict[str, str]:
    verify: dict[str, str] = {}
    if "python" in stack and any((path / d).is_dir() for d in ("tests", "test")) \
            and ((path / ".venv").exists() or (path / "venv").exists()):
        verify["test"] = "{venv_python} -m pytest -q"
    if "node" in stack:
        try:
            scripts = json.loads((path / "package.json").read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        if "test" in scripts and _NPM_DEFAULT_TEST not in scripts["test"]:
            verify["test"] = "npm test"
        if "build" in scripts:
            verify["build"] = "npm run build"
    return verify


def discover(path: Path, cfg: Config) -> Manifest:
    path = paths.resolve(str(path))
    is_git = is_git_root(path)
    repo = git_repo(path) if is_git else None
    folder = paths.nfc(path.name)
    name = folder if folder.isascii() else (repo.split("/")[1] if repo else folder)
    stack = detect_stack(path)
    health = [*scheduled_workflows(path), *deployment_checks(repo)] if repo else []

    has_cron = bool(health) or '"crons"' in _read(path / "vercel.json")
    stage = "deployed" if has_cron else ("mvp" if repo else "prototype")

    protected = [".github/workflows/", ".env*"]
    for p in ("vercel.json", "prisma/", "alembic/", "docker-compose.yml"):
        if (path / p.rstrip("/")).exists():
            protected.append(p)
    exclude = [".env*"]
    for d in sorted(path.iterdir()):
        n = d.name.lower()
        if d.is_dir() and (n == "data" or n.startswith("data_") or "backup" in n or n == "exports"):
            exclude.append(d.name + "/")
            protected.append(d.name + "/")

    return Manifest(
        id=slug(name),
        name=name,
        path=paths.display(path),
        repo=repo,
        stack=stack,
        stage=stage,
        autonomy=cfg.default_autonomy if is_git else "L0",
        verify=detect_verify(path, stack),
        health=health,
        protected_paths=protected,
        exclude=exclude,
    )


def candidate_dirs(cfg: Config) -> list[Path]:
    out = []
    for root in cfg.roots:
        r = paths.resolve(root)
        if not r.is_dir():
            continue
        for d in sorted(r.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and paths.nfc(d.name) not in cfg.ignore_dirs:
                out.append(d)
    return out


def unregistered(cfg: Config, manifests: list[Manifest]) -> list[str]:
    known = {paths.nfc(str(m.abs_path)) for m in manifests}
    return [paths.display(d) for d in candidate_dirs(cfg) if paths.nfc(str(d)) not in known]


def loose_large_files(cfg: Config, min_mb: int = 100) -> list[dict]:
    """루트 폴더에 흩어져 있는 대용량 파일(예: 백업 zip)."""
    out = []
    for root in cfg.roots:
        r = paths.resolve(root)
        if not r.is_dir():
            continue
        for f in r.iterdir():
            if f.is_file() and (size := f.stat().st_size) >= min_mb * 1024 * 1024:
                out.append({"path": paths.display(f), "size_mb": round(size / 1024 / 1024)})
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""
