"""저장소 위생: 비밀값이 새어 나갈 수 있는 상태를 찾는다.

- git에 커밋된 비밀 파일(.env, 키 파일) → 위험. 지워도 커밋 기록에 남으므로 키 교체가 필요하다.
- Vercel CLI로 연결된 프로젝트(.vercel/)인데 .vercelignore가 없거나 .env를 빼지 않음 → 주의.
  Vercel CLI는 .gitignore를 따르지 않아서, .vercelignore가 없으면 로컬 .env가 배포 소스에 그대로 올라간다.
  (2026-09 DR에서 실제로 이렇게 API 키가 배포물에 포함됐다)
"""
from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath

from .base import Check, Context, Finding, Outcome

_SECRET_NAMES = (".env", ".env.*", "*.pem", "*.p12", "*.pfx", "id_rsa", "id_ed25519")
_SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def tracked_secret_files(tracked: list[str]) -> list[str]:
    out = []
    for p in tracked:
        name = PurePosixPath(p).name
        if name.endswith(_SAFE_SUFFIXES):
            continue
        if any(fnmatch.fnmatch(name, pat) for pat in _SECRET_NAMES):
            out.append(p)
    return out


def ignores_env(ignore_text: str) -> bool:
    """ignore 파일 내용이 루트의 .env를 제외하는지 (대략적인 판단)."""
    for line in ignore_text.splitlines():
        line = line.strip().lstrip("/")
        if not line or line.startswith(("#", "!")):
            continue
        if line in ("*", ".env", ".env*", "*.env", ".*") or fnmatch.fnmatch(".env", line):
            return True
    return False


def vercel_findings(root: Path) -> list[Finding]:
    if not (root / ".vercel").is_dir():
        return []
    vi = root / ".vercelignore"
    why = ("Vercel CLI(`npx vercel`)는 .gitignore를 따르지 않고 .vercelignore만 본다. "
           "CLI로 배포하면 로컬 .env(API 키)가 배포 소스에 그대로 올라간다.")
    if not vi.exists():
        return [Finding("vercelignore_missing", "warning",
                        ".vercelignore 없음 — CLI 배포 시 .env가 올라갈 수 있음",
                        why + "\n최소한 `.env*`를 넣은 .vercelignore를 추가해야 한다.")]
    if not ignores_env(vi.read_text(encoding="utf-8", errors="replace")):
        return [Finding("vercelignore_no_env", "warning",
                        ".vercelignore가 .env를 제외하지 않음", why + "\n`.env*` 줄을 추가해야 한다.")]
    return []


class HygieneCheck(Check):
    id = "hygiene"
    label = "비밀값 위생"

    def run(self, ctx: Context) -> Outcome:
        findings = vercel_findings(ctx.path)
        if ctx.is_git:
            ls = ctx.git("ls-files", "-z")
            leaked = tracked_secret_files([p for p in ls.out.split("\0") if p])
            if leaked:
                findings.append(Finding(
                    "tracked_secrets", "critical",
                    f"비밀 파일 {len(leaked)}개가 git에 커밋돼 있음",
                    "\n".join(leaked) + "\n\n파일을 지워도 커밋 기록에 남는다. "
                    "안에 든 키를 모두 재발급하고, 파일은 .gitignore에 넣은 뒤 `git rm --cached`로 추적을 끊어야 한다.",
                ))
        summary = "문제 없음" if not findings else f"{len(findings)}건"
        return Outcome.of(summary, findings, {"vercel": (ctx.path / ".vercel").is_dir()})
