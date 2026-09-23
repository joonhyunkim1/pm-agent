"""OS 스케줄러 등록: 1시간마다 `pm tick`, 그리고 상시 Telegram 봇(`pm bot`).

실제 스캔 주기(기본 6시간)와 요약 발송 시각은 tick 안에서 판단한다. 그래서 스케줄러는
'자주, 가볍게' 깨우기만 하면 되고, 잠자기로 놓친 실행도 다음 tick에서 따라잡는다.
  macOS   launchd LaunchAgent (~/Library/LaunchAgents)
  Windows 작업 스케줄러 (schtasks) — 아직 실제 Windows에서 검증하지 않았다
"""
from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

from . import paths, proc

LABEL = "com.pm-agent.tick"
BOT_LABEL = "com.pm-agent.bot"
WIN_TASK = "pm-agent-tick"
INTERVAL_SEC = 3600


def pm_executable() -> str:
    exe = Path(sys.executable).parent / ("pm.exe" if os.name == "nt" else "pm")
    if exe.exists():
        return str(exe)
    return proc.tool("pm")


def plist_file(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def render_plist() -> bytes:
    log = paths.logs_dir() / "tick.log"
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [pm_executable(), "tick"],
        "StartInterval": INTERVAL_SEC,
        "RunAtLoad": True,
        # launchd의 기본 PATH에는 homebrew·nvm이 없어서 git/gh/npm/uv를 못 찾는다
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1"},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "ProcessType": "Background",
        "LowPriorityIO": True,
    })


def render_bot_plist() -> bytes:
    log = paths.logs_dir() / "bot.log"
    return plistlib.dumps({
        "Label": BOT_LABEL,
        "ProgramArguments": [pm_executable(), "bot"],
        "RunAtLoad": True,
        "KeepAlive": True,          # 죽으면 다시 띄운다
        "ThrottleInterval": 30,     # 연달아 죽을 때 30초 간격으로만 재시작
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1"},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "ProcessType": "Background",
    })


def install(dry_run: bool = False) -> str:
    if sys.platform == "darwin":
        jobs = [(LABEL, render_plist()), (BOT_LABEL, render_bot_plist())]
        if dry_run:
            return "\n".join(body.decode() for _, body in jobs)
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        done = []
        for label, body in jobs:
            f = plist_file(label)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)
            proc.run(["launchctl", "bootout", f"{domain}/{label}"], timeout=15)
            r = proc.run(["launchctl", "bootstrap", domain, str(f)], timeout=15)
            if not r.ok:
                raise RuntimeError(f"launchctl bootstrap 실패({label}): {r.tail(3)}")
            done.append(str(f))
        return "등록됨:\n" + "\n".join(done)
    if os.name == "nt":
        cmd = ["schtasks", "/Create", "/F", "/SC", "HOURLY", "/TN", WIN_TASK,
               "/TR", f'"{pm_executable()}" tick']
        if dry_run:
            return " ".join(cmd)
        r = proc.run(cmd, timeout=30)
        if not r.ok:
            raise RuntimeError(f"schtasks 실패: {r.tail(3)}")
        return f"작업 스케줄러에 등록됨: {WIN_TASK}"
    raise RuntimeError("이 OS의 스케줄러 등록은 아직 지원하지 않는다. cron에 `pm tick`을 1시간마다 등록하면 된다.")


def uninstall() -> str:
    if sys.platform == "darwin":
        for label in (LABEL, BOT_LABEL):
            proc.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], timeout=15)
            plist_file(label).unlink(missing_ok=True)
        return "해제됨"
    if os.name == "nt":
        proc.run(["schtasks", "/Delete", "/F", "/TN", WIN_TASK], timeout=30)
        return "해제됨"
    raise RuntimeError("지원하지 않는 OS")


def _launchd_status(label: str) -> dict:
    r = proc.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout=15)
    if not r.ok:
        return {"installed": False}
    info = {"installed": True}
    for line in r.out.splitlines():
        line = line.strip()
        for key in ("state", "last exit code", "runs", "pid"):
            if line.startswith(f"{key} ="):
                info[key] = line.split("=", 1)[1].strip()
    return info


def status() -> dict:
    if sys.platform == "darwin":
        info = _launchd_status(LABEL)
        info["bot"] = _launchd_status(BOT_LABEL)
        return info
    if os.name == "nt":
        r = proc.run(["schtasks", "/Query", "/TN", WIN_TASK], timeout=30)
        return {"installed": r.ok}
    return {"installed": False}
