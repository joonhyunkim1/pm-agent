"""의존성 체크: 업데이트 가능 버전과 알려진 취약점.

- npm: `npm outdated`, `npm audit`
- Python: 프로젝트 .venv에 실제 설치된 버전 기준으로 `uv pip list --outdated`,
  취약점은 `pip-audit`(uvx로 실행)에 freeze 결과를 넘겨 확인한다.
  pip-audit은 느려서 설치 목록이 같으면 하루 한 번만 다시 돈다.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import tomllib
from datetime import date
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from .. import proc
from .base import Check, Context, Finding, Outcome

_VER = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
# venv 안의 설치 도구 자체는 앱이 배포·실행하는 코드가 아니라서 취약점 점검에서 뺀다
_INSTALLER_TOOLS = {"pip", "setuptools", "wheel"}


def _version_key(v: str) -> tuple:
    m = _VER.match(v)
    return tuple(int(x or 0) for x in m.groups()) if m else (0,)


def bump_kind(current: str | None, latest: str | None) -> str | None:
    """'major' | 'minor' | 'patch' | None(같거나 비교 불가)."""
    if not current or not latest:
        return None
    a, b = _VER.match(current), _VER.match(latest)
    if not a or not b:
        return None
    ca = [int(x or 0) for x in a.groups()]
    cb = [int(x or 0) for x in b.groups()]
    if ca == cb:
        return None
    if ca[0] != cb[0]:
        return "major"
    if ca[1] != cb[1]:
        return "minor"
    return "patch"


def _outdated_findings(items: list[tuple[str, str, str]]) -> tuple[list[Finding], dict]:
    """items = [(이름, 현재, 최신)] → (Finding 목록, 종류별 개수)."""
    groups: dict[str, list[str]] = {"major": [], "minor": [], "patch": []}
    for name, cur, latest in items:
        kind = bump_kind(cur, latest)
        if kind:
            groups[kind].append(f"{name} {cur} → {latest}")
    findings = []
    if groups["major"]:
        findings.append(Finding("outdated_major", "info",
                                f"메이저 업데이트 가능 {len(groups['major'])}개",
                                "\n".join(sorted(groups["major"]))))
    minor = groups["minor"] + groups["patch"]
    if minor:
        findings.append(Finding("outdated_minor", "info",
                                f"마이너·패치 업데이트 가능 {len(minor)}개",
                                "\n".join(sorted(minor))))
    return findings, {k: len(v) for k, v in groups.items()}


class NpmCheck(Check):
    id = "npm"
    label = "npm 의존성"

    def run(self, ctx: Context) -> Outcome:
        if not (ctx.path / "package.json").exists():
            return Outcome("skipped", "package.json 없음")
        npm = proc.tool("npm")
        findings: list[Finding] = []
        if not (ctx.path / "node_modules").exists():
            findings.append(Finding("no_node_modules", "info", "node_modules 없음 — 설치 버전 비교 불가",
                                    "취약점은 lockfile 기준으로 점검한다."))

        r = proc.run([npm, "outdated", "--json"], cwd=ctx.path, timeout=120)
        data = _json_or_empty(r.out)
        items = []
        for name, info in data.items():
            info = info[0] if isinstance(info, list) else info
            items.append((name, info.get("current"), info.get("latest")))
        f_out, counts = _outdated_findings(items)
        findings += f_out

        r = proc.run([npm, "audit", "--json"], cwd=ctx.path, timeout=120)
        audit = _json_or_empty(r.out)
        vulns = audit.get("vulnerabilities", {})
        meta = audit.get("metadata", {}).get("vulnerabilities", {})
        by_sev: dict[str, list[str]] = {}
        for name, v in vulns.items():
            fix = v.get("fixAvailable")
            fix_txt = ("수정 가능" if fix is True else
                       f"수정: {fix.get('name')}@{fix.get('version')}"
                       + (" (메이저)" if fix.get("isSemVerMajor") else "")
                       if isinstance(fix, dict) else "수정 버전 없음")
            direct = "직접" if v.get("isDirect") else "간접"
            by_sev.setdefault(v.get("severity", "low"), []).append(f"{name} ({direct}, {fix_txt})")
        if by_sev.get("critical"):
            findings.append(Finding("vuln_critical", "critical",
                                    f"심각(critical) 취약점 {len(by_sev['critical'])}개",
                                    "\n".join(by_sev["critical"])))
        if by_sev.get("high"):
            findings.append(Finding("vuln_high", "warning", f"높음(high) 취약점 {len(by_sev['high'])}개",
                                    "\n".join(by_sev["high"])))
        low = by_sev.get("moderate", []) + by_sev.get("low", [])
        if low:
            findings.append(Finding("vuln_moderate", "info", f"중간·낮음 취약점 {len(low)}개",
                                    "\n".join(low)))

        vuln_total = meta.get("total", sum(len(v) for v in by_sev.values()))
        summary = f"업데이트 {sum(counts.values())}개(메이저 {counts['major']}) · 취약점 {vuln_total}개"
        return Outcome.of(summary, findings, {"outdated": counts, "vulnerabilities": meta})


def direct_requirements(root: Path) -> set[str]:
    """requirements*.txt(루트와 한 단계 아래)와 pyproject.toml에 적힌 직접 의존성 이름."""
    names: set[str] = set()
    files = [*root.glob("requirements*.txt"), *root.glob("*/requirements*.txt")]
    for f in files:
        if any(part.startswith(".") or part in ("node_modules", "venv") for part in f.parts[-2:-1]):
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            try:
                names.add(canonicalize_name(Requirement(line).name))
            except InvalidRequirement:
                pass
    pp = root / "pyproject.toml"
    if pp.exists():
        try:
            deps = tomllib.loads(pp.read_text(encoding="utf-8")).get("project", {}).get("dependencies", [])
        except tomllib.TOMLDecodeError:
            deps = []
        for d in deps:
            try:
                names.add(canonicalize_name(Requirement(d).name))
            except InvalidRequirement:
                pass
    return names


class PythonDepsCheck(Check):
    id = "python"
    label = "Python 의존성"

    def run(self, ctx: Context) -> Outcome:
        py = ctx.venv_python()
        if not py:
            return Outcome("skipped", ".venv 없음 — 설치된 버전을 알 수 없음",
                           findings=[Finding("no_venv", "info", "가상환경(.venv) 없음",
                                             "설치 버전 기준 점검과 테스트 실행을 하려면 .venv가 필요하다.")])
        uv = proc.tool("uv")
        direct = direct_requirements(ctx.path)
        findings: list[Finding] = []

        r = proc.run([uv, "pip", "list", "--python", str(py), "--outdated", "--format", "json"],
                     cwd=ctx.path, timeout=180)
        items = []
        for p in _json_or_empty(r.out, default=[]):
            if not direct or canonicalize_name(p["name"]) in direct:
                items.append((p["name"], p.get("version"), p.get("latest_version")))
        f_out, counts = _outdated_findings(items)
        findings += f_out

        vulns, audit_error = self._audit(ctx, uv, py)
        if audit_error:
            findings.append(Finding("audit_error", "info", "취약점 검사를 끝내지 못함", audit_error))
        n_vuln = 0
        if vulns:
            lines = []
            for d in vulns:
                ids = sorted({v["id"] for v in d["vulns"]})
                n_vuln += len(ids)
                fixes = sorted({fv for v in d["vulns"] for fv in v.get("fix_versions", [])},
                               key=_version_key)
                scope = "직접" if canonicalize_name(d["name"]) in direct else "간접"
                lines.append(f"{d['name']} {d['version']} ({scope}): {', '.join(ids)}"
                             + (f" → 수정 {fixes[-1]}" if fixes else ""))
            findings.append(Finding("vulns", "warning",
                                    f"알려진 취약점 {n_vuln}건 ({len(vulns)}개 패키지)",
                                    "\n".join(lines)))

        summary = f"업데이트 {sum(counts.values())}개(메이저 {counts['major']}) · 취약점 {n_vuln}건"
        return Outcome.of(summary, findings, {"outdated": counts, "vulnerabilities": n_vuln,
                                              "direct": len(direct)})

    def _audit(self, ctx: Context, uv: str, py: Path) -> tuple[list[dict], str | None]:
        freeze = proc.run([uv, "pip", "freeze", "--python", str(py)], cwd=ctx.path, timeout=60)
        pinned = sorted(
            l for l in freeze.out.splitlines()
            if "==" in l and not l.startswith("-")
            and canonicalize_name(l.split("==", 1)[0]) not in _INSTALLER_TOOLS
        )
        if not pinned:
            return [], None
        digest = hashlib.sha1("\n".join(pinned).encode()).hexdigest()
        key = f"pyaudit:{ctx.manifest.id}"
        cached = ctx.store.get_kv(key)
        today = date.today().isoformat()
        if cached and cached["hash"] == digest and cached["day"] == today:
            return cached["vulns"], None

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
            tf.write("\n".join(pinned))
            req = tf.name
        try:
            r = proc.run([proc.tool("uvx"), "--quiet", "pip-audit", "-r", req, "--no-deps",
                          "--disable-pip", "--format", "json", "--progress-spinner", "off"],
                         cwd=ctx.path, timeout=300)
        finally:
            Path(req).unlink(missing_ok=True)
        data = _json_or_empty(r.out)
        if not data:
            return [], (r.tail(5) or "pip-audit 결과 없음")
        vulns = [d for d in data.get("dependencies", []) if d.get("vulns")]
        ctx.store.set_kv(key, {"hash": digest, "day": today, "vulns": vulns})
        return vulns, None


def _json_or_empty(text: str, default=None):
    default = {} if default is None else default
    text = text.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default
