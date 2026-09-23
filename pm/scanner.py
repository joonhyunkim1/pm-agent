"""스캔 오케스트레이션.

매니페스트마다 체크를 돌려 결과와 Finding을 저장하고, 새 위험 신호를 알린다.
스캔은 LLM을 쓰지 않으므로 비용이 0이다. 동시에 두 스캔이 돌지 않도록 잠금 파일을 쓴다.
"""
from __future__ import annotations

import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from . import manifest as mf
from . import notify, paths, proc
from .checks import Context, Finding, Outcome, build_checks
from .config import Config, load_config
from .store import Store, now_iso, parse_iso

LOCK_STALE_SEC = 60 * 60
Log = Callable[[str], None]


class ScanBusy(Exception):
    pass


@contextmanager
def scan_lock(name: str = "scan"):
    f = paths.lock_file(name)
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists() and time.time() - f.stat().st_mtime > LOCK_STALE_SEC:
        f.unlink(missing_ok=True)
    try:
        fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ScanBusy(f"다른 작업({name})이 진행 중이다") from None
    try:
        os.write(fd, f"{os.getpid()} {now_iso()}".encode())
        os.close(fd)
        yield
    finally:
        f.unlink(missing_ok=True)


def is_scanning(name: str = "scan") -> bool:
    f = paths.lock_file(name)
    return f.exists() and time.time() - f.stat().st_mtime <= LOCK_STALE_SEC


def _run_check(check, ctx: Context) -> tuple[Outcome, int]:
    t0 = time.monotonic()
    try:
        out = check.run(ctx)
    except proc.ToolMissing as e:
        out = Outcome("error", f"'{e}' 명령 없음", findings=[Finding(
            "__error__", "warning", f"{check.label}: '{e}' 명령을 찾을 수 없음",
            f"{e}를 설치하거나 PATH에 추가해야 이 체크가 동작한다.")])
    except Exception as e:  # 체크 하나의 실패가 스캔 전체를 멈추지 않게 한다
        out = Outcome("error", f"{type(e).__name__}: {e}"[:200], findings=[Finding(
            "__error__", "warning", f"{check.label}: 체크 실행 실패",
            "".join(traceback.format_exception(e))[-1500:])])
    return out, int((time.monotonic() - t0) * 1000)


def _scan_project(m: mf.Manifest, cfg: Config, http: httpx.Client, run_id: int, log: Log,
                  light_only: bool = False) -> dict:
    with Store() as st:
        if not m.abs_path.is_dir():
            out = Outcome.of("폴더 없음", [Finding("missing_path", "critical",
                                                 f"프로젝트 폴더를 찾을 수 없음: {m.path}")])
            st.add_result(run_id, m.id, "path", "프로젝트 경로", out, 0)
            st.apply_findings(m.id, "path", out)
            return {"status": out.status, "checks": 1}
        st.apply_findings(m.id, "path", Outcome.of("ok"))

        ctx = Context(manifest=m, config=cfg, store=st, http=http)
        statuses = []
        checks = [c for c in build_checks(m) if c.light or not light_only]
        for check in checks:
            out, ms = _run_check(check, ctx)
            st.add_result(run_id, m.id, check.id, check.label, out, ms)
            st.apply_findings(m.id, check.id, out)
            statuses.append(out.status)
            log(f"  [{m.name}] {check.label}: {out.status} — {out.summary} ({ms / 1000:.1f}s)")
        worst = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "ok"
        return {"status": worst, "checks": len(statuses)}


def scan(trigger: str = "manual", only: list[str] | None = None, notify_: bool = True,
         log: Log = lambda s: None, light_only: bool = False) -> dict:
    """light_only=True면 운영 헬스체크(가벼운 체크)만 돌린다."""
    cfg = load_config()
    manifests, errors = mf.load_all()
    for e in errors:
        log(f"매니페스트 오류: {e}")
    if only:
        manifests = [m for m in manifests if m.id in only]

    with scan_lock(), Store() as st:
        run_id = st.start_run(trigger)
        started = time.monotonic()
        per_project: dict[str, dict] = {}
        st.set_kv("scan_progress", {"run_id": run_id, "done": 0, "total": len(manifests)})
        with httpx.Client(follow_redirects=True, headers={"User-Agent": "pm-agent"}) as http, \
                ThreadPoolExecutor(max_workers=4) as pool:
            futs = {pool.submit(_scan_project, m, cfg, http, run_id, log, light_only): m for m in manifests}
            for fut in as_completed(futs):
                m = futs[fut]
                try:
                    per_project[m.id] = fut.result()
                except Exception as e:
                    per_project[m.id] = {"status": "error", "error": str(e)}
                    log(f"  [{m.name}] 스캔 실패: {e}")
                st.set_kv("scan_progress", {"run_id": run_id, "done": len(per_project),
                                            "total": len(manifests)})
        summary = {
            "projects": per_project,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "manifest_errors": errors,
            "light": light_only,
        }
        st.finish_run(run_id, "ok", summary)
        st.prune()

        sent = 0
        if notify_:
            try:
                sent = notify.push_alerts(st, manifests, cfg)
            except Exception as e:
                log(f"알림 전송 실패: {e}")
        summary["alerts_sent"] = sent
        return {"run_id": run_id, **summary}


def scan_due(st: Store, cfg: Config) -> bool:
    last = st.last_finished_run(full_only=True)
    if not last:
        return True
    age = datetime.now(timezone.utc) - parse_iso(last["finished_at"])
    return age >= timedelta(hours=cfg.scan_interval_hours)


def tick(log: Log = print) -> None:
    """스케줄러가 1시간마다 부른다.

    - 전체 스캔 주기(기본 6시간)가 지났으면 전체 스캔
    - 아니면 운영 헬스체크만 도는 가벼운 스캔 (서비스 장애를 최대 1시간 안에 잡기 위해)
    - 하루 한 번 요약 발송
    노트북이 잠들어 있다 깨어나도 다음 tick에서 밀린 스캔을 따라잡는다.
    """
    cfg = load_config()
    with Store() as st:
        due = scan_due(st, cfg)
    try:
        if due:
            r = scan("schedule", log=log)
        else:
            r = scan("schedule-light", log=log, light_only=True)
        log(f"{'전체' if due else '헬스'} 스캔 완료: run #{r['run_id']}, 알림 {r['alerts_sent']}건")
    except ScanBusy:
        log("다른 스캔이 진행 중이라 건너뜀")

    # 제안(P1): 주기가 됐고 키가 있으면, 변화가 있는 프로젝트만 LLM에 보낸다
    from . import llm, planner

    with Store() as st:
        due_plan = planner.plan_due(st, cfg)
    if due_plan and llm.configured():
        try:
            r = planner.plan("schedule", log=log)
            log(f"계획 완료: 새 제안 {sum(r['created'].values())}개, 비용 ${r['cost_usd']:.4f}, "
                f"이번 달 ${r['month_spend']:.2f}")
        except ScanBusy:
            log("다른 계획이 진행 중이라 건너뜀")
        except Exception as e:
            log(f"계획 실패: {type(e).__name__}: {e}")
    with Store() as st:
        manifests, _ = mf.load_all()
        try:
            if notify.maybe_digest(st, manifests, cfg):
                log("일일 요약 전송")
        except Exception as e:
            log(f"일일 요약 전송 실패: {e}")
