"""외부 명령 실행 헬퍼.

모든 호출에 타임아웃을 두고, 타임아웃 시 자식 프로세스까지 정리한다.
실행 파일은 shutil.which로 찾는다(Windows의 npm.cmd 같은 확장자 대응).
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass


class ToolMissing(Exception):
    pass


@dataclass
class Result:
    code: int
    out: str
    err: str
    duration_ms: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out

    def tail(self, n: int = 30) -> str:
        text = (self.out + ("\n" + self.err if self.err else "")).strip()
        return "\n".join(text.splitlines()[-n:])


def tool(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise ToolMissing(name)
    return p


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(p.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run(
    args: list[str] | str,
    cwd=None,
    timeout: float = 60,
    shell: bool = False,
    env: dict | None = None,
) -> Result:
    start = time.monotonic()
    full_env = {**os.environ, "NO_COLOR": "1", **(env or {})}
    kw: dict = {}
    if os.name == "posix":
        kw["start_new_session"] = True
    else:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(
        args,
        cwd=cwd,
        shell=shell,
        env=full_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kw,
    )
    try:
        out, err = p.communicate(timeout=timeout)
        return Result(p.returncode, out, err, _ms(start))
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        out, err = p.communicate()
        return Result(-1, out or "", err or "", _ms(start), timed_out=True)


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
