"""Git 상태: 미커밋·미푸시 변경, 원격 대비 뒤처짐, 대용량 파일."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .base import Check, Context, Finding, Outcome

STALE_DIRTY_DAYS = 7
STALE_UNPUSHED_DAYS = 3


class GitCheck(Check):
    id = "git"
    label = "Git 상태"

    def run(self, ctx: Context) -> Outcome:
        if not ctx.is_git:
            return Outcome.of(
                "git 저장소 아님",
                [Finding("not_git", "info", "git 저장소가 아님",
                         "버전 관리가 없어서 변경 이력과 롤백이 불가능하다. 자동 수정 대상에서 제외된다.")],
                {"is_git": False},
            )

        findings: list[Finding] = []
        has_remote = bool(ctx.git("remote").out.strip())
        fetched = None
        if ctx.config.git_fetch and has_remote:
            fetched = ctx.git("fetch", "--quiet", "--no-tags", timeout=45).ok

        branch = ctx.git("branch", "--show-current").out.strip() or "(detached)"

        # 미커밋 변경: 가장 오래된 변경 파일의 수정 시각으로 '얼마나 방치됐는지'를 본다
        dirty = ctx.dirty_paths()
        oldest_days = None
        if dirty:
            mtimes = []
            for p in dirty:
                fp = ctx.path / p
                if fp.exists():
                    mtimes.append(fp.stat().st_mtime)
            if mtimes:
                oldest_days = (time.time() - min(mtimes)) / 86400
            stale = oldest_days is not None and oldest_days >= STALE_DIRTY_DAYS
            age = f", 가장 오래된 변경 {oldest_days:.0f}일 전" if oldest_days is not None else ""
            findings.append(Finding(
                "dirty",
                "warning" if stale else "info",
                f"미커밋 변경 {len(dirty)}개{age}",
                "\n".join(dirty[:15]) + (f"\n… 외 {len(dirty) - 15}개" if len(dirty) > 15 else ""),
            ))

        # 원격 대비
        ahead = behind = None
        ab = ctx.git("rev-list", "--left-right", "--count", "HEAD...@{u}")
        if ab.ok:
            a, b = ab.out.split()
            ahead, behind = int(a), int(b)
        elif has_remote:
            findings.append(Finding("no_upstream", "info", f"'{branch}' 브랜치에 원격 추적 브랜치가 없음"))
        else:
            findings.append(Finding("no_remote", "info", "원격 저장소 없음",
                                    "이 PC가 고장 나면 코드가 사라진다. 비공개 원격 저장소를 권장한다."))

        if ahead:
            ts = ctx.git("log", "@{u}..HEAD", "--format=%ct").out.split()
            days = (time.time() - int(ts[-1])) / 86400 if ts else 0
            findings.append(Finding(
                "unpushed",
                "warning" if days >= STALE_UNPUSHED_DAYS else "info",
                f"푸시 안 된 커밋 {ahead}개 (가장 오래된 것 {days:.0f}일 전)",
            ))
        if behind:
            findings.append(Finding(
                "behind", "info", f"원격보다 {behind}커밋 뒤처짐",
                "자동 수정 전에 pull이 필요하다.",
            ))

        # 대용량 파일 (추적 중 + 무시되지 않은 미추적 파일)
        limit = ctx.config.large_file_mb * 1024 * 1024
        big = []
        ls = ctx.git("ls-files", "-z", "-co", "--exclude-standard", timeout=60)
        for p in filter(None, ls.out.split("\0")):
            fp = ctx.path / p
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if size >= limit:
                big.append((p, size))
        if big:
            big.sort(key=lambda x: -x[1])
            findings.append(Finding(
                "large_files", "warning",
                f"{ctx.config.large_file_mb}MB 넘는 파일 {len(big)}개",
                "\n".join(f"{p} ({s / 1024 / 1024:.0f}MB)" for p, s in big[:10]),
            ))

        last = ctx.git("log", "-1", "--format=%cI%x1f%s")
        last_at, last_msg = (last.out.strip().split("\x1f", 1) + [""])[:2] if last.ok else (None, "")

        value = {
            "is_git": True,
            "branch": branch,
            "head": (ctx.head() or "")[:8],
            "dirty": len(dirty),
            "oldest_dirty_days": round(oldest_days, 1) if oldest_days is not None else None,
            "ahead": ahead,
            "behind": behind,
            "fetched": fetched,
            "last_commit_at": last_at,
            "last_commit_msg": last_msg,
        }
        parts = [branch, f"미커밋 {len(dirty)}"]
        if ahead:
            parts.append(f"↑{ahead}")
        if behind:
            parts.append(f"↓{behind}")
        if last_at:
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).days
            parts.append(f"마지막 커밋 {days}일 전" if days else "오늘 커밋")
        return Outcome.of(" · ".join(parts), findings, value)
