"""대시보드용 로컬 HTTP API. 127.0.0.1에서만 연다.

코어 엔진과 UI를 HTTP로 분리해 두면, 나중에 Tauri로 감싸 데스크톱 앱으로 배포할 때
이 API를 그대로 쓸 수 있다.
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import discovery, llm, notify, planner, pricing, proposals, scanner, schedule
from . import manifest as mf
from .config import load_config
from .notify import telegram
from .store import Store

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

app = FastAPI(title="pm-agent", docs_url="/api/docs", openapi_url="/api/openapi.json")
# DNS rebinding 방지: 로컬 호스트 이름으로 들어온 요청만 받는다
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

_scan_error: dict = {}
_plan_state: dict = {}


def _summary(m: mf.Manifest, active: list[dict], results: list[dict], waiting: int = 0) -> dict:
    counts = {s: sum(1 for f in active if f["severity"] == s) for s in ("critical", "warning", "info")}
    git = next((r["value"] for r in results if r["check_id"] == "git"), None)
    last = max((r["created_at"] for r in results), default=None)
    top = next((f for f in active if f["severity"] != "info"), None)
    return {
        "id": m.id,
        "name": m.name,
        "path": m.path,
        "repo": m.repo,
        "stage": m.stage,
        "stack": m.stack,
        "goal": m.goal,
        "autonomy": m.autonomy,
        "effective_autonomy": m.effective_autonomy,
        "verification": m.verification_level,
        "health": notify.project_health(active) if results else "gray",
        "counts": counts,
        "unacked": sum(1 for f in active if f["status"] == "open" and f["severity"] != "info"),
        "top": top,
        "git": git,
        "last_checked": last,
        "proposals_waiting": waiting,
    }


@app.get("/api/overview")
def overview() -> dict:
    cfg = load_config()
    manifests, errors = mf.load_all()
    with Store() as st:
        results = st.latest_results()
        active = st.findings(statuses=("open", "acknowledged"))
        waiting = st.proposals(statuses=("proposed", "deferred"))
        projects = [
            _summary(m, [f for f in active if f["project_id"] == m.id],
                     [r for r in results if r["project_id"] == m.id],
                     sum(1 for p in waiting if p["project_id"] == m.id))
            for m in manifests
        ]
        return {
            "projects": projects,
            "ideas": st.ideas(),
            "last_run": st.last_finished_run(),
            "scanning": scanner.is_scanning(),
            "progress": st.get_kv("scan_progress"),
            "scan_error": _scan_error.get("message"),
            "unregistered": discovery.unregistered(cfg, manifests),
            "loose_files": discovery.loose_large_files(cfg),
            "manifest_errors": errors,
            "telegram": telegram.configured(),
            "schedule": schedule.status(),
            "stages": list(mf.STAGES),
            "llm": _llm_summary(st, cfg),
            "proposals_waiting": len(waiting),
        }


def _llm_summary(st: Store, cfg) -> dict:
    return {
        "configured": llm.configured(),
        "planner_model": cfg.llm.planner_model,
        "month_spend": round(st.month_spend(), 4),
        "budget": cfg.llm.monthly_budget_usd,
        "last_plan_at": st.get_kv("last_plan_at"),
        "planning": scanner.is_scanning("plan"),
        "plan_error": _plan_state.get("error"),
        "last_plan": _plan_state.get("report"),
        "pricing": pricing.meta(),
    }


@app.get("/api/projects/{pid}")
def project(pid: str) -> dict:
    manifests, _ = mf.load_all()
    m = next((x for x in manifests if x.id == pid), None)
    if not m:
        raise HTTPException(404, "프로젝트 없음")
    with Store() as st:
        results = st.latest_results(pid)
        findings = st.findings(pid)
        active = [f for f in findings if f["status"] in ("open", "acknowledged")]
        return {
            "project": _summary(m, active, results),
            "manifest": m.model_dump(mode="json", exclude_none=True),
            "manifest_file": str(mf.manifest_file(pid)),
            "checks": results,
            "findings": findings,
            "events": st.events(pid, limit=40),
            "proposals": st.proposals(pid, limit=30),
        }


@app.get("/api/findings")
def findings(status: str = "active") -> list[dict]:
    statuses = ("open", "acknowledged") if status == "active" else tuple(status.split(","))
    with Store() as st:
        return st.findings(statuses=statuses)


class StatusBody(BaseModel):
    status: str


@app.post("/api/findings/{fp}")
def set_status(fp: str, body: StatusBody) -> dict:
    if body.status not in ("open", "acknowledged", "ignored"):
        raise HTTPException(400, "status는 open | acknowledged | ignored")
    with Store() as st:
        if not st.set_finding_status(fp, body.status):
            raise HTTPException(404, "변경할 수 없는 항목")
        return st.finding(fp)


class ScanBody(BaseModel):
    projects: list[str] | None = None


@app.post("/api/scan")
def start_scan(body: ScanBody | None = None) -> dict:
    if scanner.is_scanning():
        raise HTTPException(409, "이미 스캔 중")
    only = body.projects if body else None

    def work():
        _scan_error.clear()
        try:
            scanner.scan("dashboard", only=only)
        except Exception as e:
            _scan_error["message"] = str(e)

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/api/runs")
def runs(limit: int = 30) -> list[dict]:
    with Store() as st:
        return st.runs(limit)


class IdeaBody(BaseModel):
    title: str
    note: str = ""


@app.post("/api/ideas")
def add_idea(body: IdeaBody) -> dict:
    if not body.title.strip():
        raise HTTPException(400, "제목이 비었음")
    with Store() as st:
        return {"id": st.add_idea(body.title.strip(), body.note.strip())}


@app.delete("/api/ideas/{idea_id}")
def archive_idea(idea_id: int) -> dict:
    with Store() as st:
        st.archive_idea(idea_id)
    return {"ok": True}


# ---- 제안 (P1) ----
_STATUS_GROUPS = {"open": ("proposed", "deferred", "backlog"), "waiting": ("proposed", "deferred"), "all": None}


@app.get("/api/proposals")
def list_proposals(status: str = "open") -> list[dict]:
    statuses = _STATUS_GROUPS.get(status, tuple(status.split(",")))
    with Store() as st:
        return st.proposals(statuses=statuses)


class DecisionBody(BaseModel):
    action: str
    note: str = ""


@app.post("/api/proposals/{pid}")
def decide(pid: str, body: DecisionBody) -> dict:
    if body.action not in proposals.ACTIONS:
        raise HTTPException(400, "action은 approve | reject | defer")
    with Store() as st:
        p = proposals.decide(st, pid, body.action, "dashboard", body.note.strip())
        if not p:
            raise HTTPException(409, "없는 제안이거나 이미 결정된 제안")
        return p


@app.get("/api/proposals/{pid}/task")
def task(pid: str) -> dict:
    with Store() as st:
        p = st.proposal(pid)
    if not p:
        raise HTTPException(404, "없는 제안")
    f = proposals.work_order_path(pid)
    md = f.read_text(encoding="utf-8") if f.exists() else proposals.work_order(p, proposals._manifest(p["project_id"]))
    return {"markdown": md, "approved": f.exists(), "path": str(f) if f.exists() else None}


@app.get("/api/plan/estimate")
def plan_estimate(force: bool = False) -> dict:
    return planner.estimate(force=force)


class PlanBody(BaseModel):
    projects: list[str] | None = None
    force: bool = False


@app.post("/api/plan")
def start_plan(body: PlanBody | None = None) -> dict:
    if scanner.is_scanning("plan"):
        raise HTTPException(409, "이미 계획 중")
    if not llm.configured():
        raise HTTPException(400, f"{llm.KEY} 미설정 — 터미널에서 `pm secret set {llm.KEY}`")
    body = body or PlanBody()

    def work():
        _plan_state.clear()
        try:
            _plan_state["report"] = planner.plan("dashboard", only=body.projects, force=body.force)
        except Exception as e:
            _plan_state["error"] = f"{type(e).__name__}: {e}"

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/api/llm/calls")
def llm_calls(limit: int = 50) -> list[dict]:
    with Store() as st:
        return st.llm_calls(limit)


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse)
    def no_web() -> str:
        return ("<p>대시보드가 아직 빌드되지 않았습니다. "
                "<code>cd web && npm install && npm run build</code> 후 다시 여세요.</p>")
