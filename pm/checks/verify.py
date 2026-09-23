"""검증 명령(test/build 등) 실행.

- 작업 트리에 변경이 있으면 돌리지 않는다. 사람이 작업 중인 코드를 검증해 봐야
  의미가 없고, L3 자동 머지 규칙(작업 트리가 clean일 때만)과도 맞춘다.
- 같은 커밋(HEAD)에서는 다시 돌리지 않고 지난 결과를 재사용한다.
"""
from __future__ import annotations

import os
import shlex

from .. import proc
from ..manifest import VerifyCommand
from .base import Check, Context, Finding, Outcome


class Verify(Check):
    def __init__(self, name: str, cmd: VerifyCommand):
        self.name = name
        self.cmd = cmd
        self.id = f"verify:{name}"
        self.label = f"검증 {name}"

    def run(self, ctx: Context) -> Outcome:
        head = ctx.head() if ctx.is_git else None
        if ctx.is_git and ctx.dirty_paths():
            return Outcome("skipped", "작업 중인 변경이 있어 검증 보류", {"reason": "dirty"})

        # 의존성이 설치되지 않은 상태의 실패는 코드 문제가 아니라 환경 문제라서 실행하지 않는다.
        # (이 경우 '검증할 수 없음'이 현재 상태이므로, 이전 실패 기록은 이 결과로 대체된다)
        command = self.cmd.run
        if command.split()[0] in ("npm", "npx", "pnpm", "yarn") and not (ctx.path / "node_modules").exists():
            return Outcome.of("node_modules 없음 — 검증 불가", [Finding(
                "deps_missing", "warning", f"{self.name}: 의존성이 설치되지 않아 실행할 수 없음",
                "프로젝트 폴더에서 `npm ci`를 실행하면 다음 스캔부터 검증한다.")])
        if "{venv_python}" in command:
            py = ctx.venv_python()
            if not py:
                return Outcome.of(".venv 없음 — 검증 불가", [Finding(
                    "no_venv", "warning", f"{self.name}: .venv가 없어 실행할 수 없음")])
            quoted = shlex.quote(str(py)) if os.name == "posix" else f'"{py}"'
            command = command.replace("{venv_python}", quoted)

        key = f"verify:{ctx.manifest.id}:{self.name}"
        cached = ctx.store.get_kv(key)
        if head and cached and cached["head"] == head and cached["run"] == self.cmd.run:
            out = Outcome.from_dict(cached["outcome"])
            out.value["cached"] = True
            return out

        r = proc.run(command, cwd=ctx.path, timeout=self.cmd.timeout_sec, shell=True)
        secs = r.duration_ms / 1000
        value = {"head": (head or "")[:8], "duration_ms": r.duration_ms, "exit": r.code}
        if r.timed_out:
            # 타임아웃은 환경 문제일 수 있어서 캐시하지 않는다
            return Outcome.of(f"{self.name} 시간 초과 ({self.cmd.timeout_sec}s)", [Finding(
                "timeout", "warning", f"{self.name} 시간 초과 ({self.cmd.timeout_sec}초)", r.tail(15))],
                value)
        if r.ok:
            out = Outcome.of(f"{self.name} 통과 ({secs:.0f}s)", [], value)
        else:
            value["tail"] = r.tail(20)
            out = Outcome.of(f"{self.name} 실패 (exit {r.code}, {secs:.0f}s)", [Finding(
                "failed", self.cmd.severity, f"{self.name} 실패 (exit {r.code})", r.tail(25))], value)
        if head:
            ctx.store.set_kv(key, {"head": head, "run": self.cmd.run, "outcome": out.to_dict()})
        return out
