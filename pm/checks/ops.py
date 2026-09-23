"""운영 헬스체크: GitHub Actions 예약 워크플로우, HTTP JSON 헬스 엔드포인트."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from .. import proc, secrets
from ..manifest import GhDeploymentCheck, GhWorkflowCheck, HttpJsonCheck
from ..store import parse_iso
from .base import Check, Context, Finding, Outcome

_TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")
_ERR = re.compile(r"(Error|Exception|error:|FAILED|Traceback|fatal:)")


class GhWorkflow(Check):
    light = True

    def __init__(self, spec: GhWorkflowCheck):
        self.spec = spec
        self.id = f"gh:{spec.workflow}"
        self.label = f"워크플로우 {spec.workflow}"

    def run(self, ctx: Context) -> Outcome:
        repo = ctx.manifest.repo
        if not repo:
            return Outcome("skipped", "repo 미설정",
                           findings=[Finding("no_repo", "warning", "매니페스트에 repo(owner/name)가 없음")])
        gh = proc.tool("gh")
        spec = self.spec
        r = proc.run([gh, "run", "list", "-R", repo, "-w", spec.workflow, "-L", str(spec.window),
                      "--json", "databaseId,conclusion,status,createdAt,url,event"], timeout=60)
        if not r.ok:
            raise RuntimeError(f"gh run list 실패: {r.tail(3)}")
        runs = json.loads(r.out or "[]")
        st = proc.run([gh, "api", f"repos/{repo}/actions/workflows/{spec.workflow}", "--jq", ".state"],
                      timeout=30)
        state = st.out.strip() if st.ok else None

        done = [x for x in runs if x["status"] == "completed" and x["conclusion"] not in ("cancelled", "skipped")]
        failed = [x for x in done if x["conclusion"] != "success"]
        rate = len(failed) / len(done) if done else 0.0
        last_ok = next((x for x in done if x["conclusion"] == "success"), None)
        age_h = _hours_since(last_ok["createdAt"]) if last_ok else None

        findings: list[Finding] = []
        if state and state != "active":
            detail = ("GitHub은 60일간 커밋이 없는 public 레포의 예약 워크플로우를 자동으로 끈다. "
                      "Actions 탭에서 다시 켜야 한다." if state == "disabled_inactivity" else "")
            findings.append(Finding("disabled", "critical", f"워크플로우가 비활성화됨 ({state})", detail))

        latest_fail = failed[0] if failed else None
        if latest_fail and len(done) >= 3 and rate >= spec.max_failure_rate:
            findings.append(Finding(
                "failure_rate", "critical",
                f"{spec.workflow} 실패율 {rate:.0%} (최근 {len(done)}회 중 {len(failed)}회)",
                _error_hint(ctx, gh, repo, latest_fail["databaseId"]),
                latest_fail["url"],
            ))
        elif done and done[0]["conclusion"] != "success":
            findings.append(Finding(
                "last_failed", "warning", f"{spec.workflow} 최근 실행 실패",
                _error_hint(ctx, gh, repo, done[0]["databaseId"]), done[0]["url"],
            ))

        if spec.max_age_hours and done:
            if age_h is None:
                findings.append(Finding("stale", "critical",
                                        f"{spec.workflow} 최근 {len(done)}회 동안 성공 없음"))
            elif age_h > spec.max_age_hours:
                findings.append(Finding("stale", "critical",
                                        f"{spec.workflow} 마지막 성공 {age_h:.0f}시간 전 "
                                        f"(기준 {spec.max_age_hours:.0f}시간)"))

        value = {
            "runs": [{"t": x["createdAt"], "c": x["conclusion"] or x["status"], "url": x["url"]}
                     for x in reversed(runs)],
            "failure_rate": round(rate, 3),
            "last_success": last_ok["createdAt"] if last_ok else None,
            "state": state,
        }
        if not done:
            summary = "실행 기록 없음"
        else:
            ok_txt = f"마지막 성공 {age_h:.0f}시간 전" if age_h is not None else "성공 기록 없음"
            summary = f"성공률 {1 - rate:.0%} ({len(done) - len(failed)}/{len(done)}) · {ok_txt}"
        return Outcome.of(summary, findings, value)


# GitHub은 새 배포가 성공하면 이전 성공 배포를 inactive로 바꾼다. 둘 다 '성공했던 배포'다.
_DEPLOY_OK = ("success", "inactive")
_DEPLOY_FAIL = ("failure", "error")


def consecutive_failures(states: list[str]) -> int:
    """최신 순 상태 목록에서, 맨 앞부터 연속된 실패 횟수. 진행 중인 배포는 건너뛴다."""
    n = 0
    for s in states:
        if s in _DEPLOY_FAIL:
            n += 1
        elif s in _DEPLOY_OK:
            break
    return n


class GhDeployment(Check):
    """GitHub Deployments 기록으로 배포 파이프라인이 살아 있는지 본다.

    빌드가 실패해도 이전 버전이 계속 서비스되기 때문에, 서비스 헬스체크만으로는
    '새 코드가 반영되지 않는 상태'를 알아챌 수 없다. 이 체크가 그 틈을 메운다.
    CLI로 직접 올린 배포는 GitHub 기록에 남지 않는다는 점에 주의.
    """

    light = True

    def __init__(self, spec: GhDeploymentCheck):
        self.spec = spec
        self.id = f"deploy:{spec.environment}"
        self.label = f"배포 {spec.environment}"

    def run(self, ctx: Context) -> Outcome:
        repo = ctx.manifest.repo
        if not repo:
            return Outcome("skipped", "repo 미설정")
        gh = proc.tool("gh")
        env = self.spec.environment
        r = proc.run([gh, "api", f"repos/{repo}/deployments?environment={env}&per_page={self.spec.window}"],
                     timeout=30)
        if not r.ok:
            raise RuntimeError(f"gh api deployments 실패: {r.tail(3)}")
        deps = json.loads(r.out or "[]")
        if not deps:
            return Outcome.of(f"{env} 배포 기록 없음", [], {"runs": []})

        items = []
        for d in deps:
            s = proc.run([gh, "api", f"repos/{repo}/deployments/{d['id']}/statuses?per_page=1"], timeout=30)
            st = (json.loads(s.out or "[]") or [{}])[0] if s.ok else {}
            items.append({
                "t": d["created_at"],
                "sha": d["sha"][:7],
                "state": st.get("state", "unknown"),
                "description": st.get("description") or "",
                "url": st.get("log_url") or st.get("target_url") or st.get("environment_url"),
            })

        states = [x["state"] for x in items]
        fails = consecutive_failures(states)
        last_ok = next((x for x in items if x["state"] in _DEPLOY_OK), None)
        findings: list[Finding] = []
        if fails:
            latest = items[0]
            days = _hours_since(latest["t"]) / 24
            since = f"{len(items)}회 이상" if fails == len(items) else f"{fails}회"
            findings.append(Finding(
                "failing", "warning",
                f"{env} 배포 {since} 연속 실패 (마지막 시도 {days:.0f}일 전)",
                "서비스는 마지막으로 성공한 버전으로 계속 돌지만, 그 뒤의 커밋은 반영되지 않는다.\n"
                "CLI로 직접 배포한 버전은 이 기록에 나타나지 않는다.\n\n"
                + "\n".join(f"{x['t'][:10]} {x['sha']} {x['state']}" for x in items[:fails])
                + (f"\n\n{latest['description']}" if latest["description"] else ""),
                latest["url"],
            ))

        value = {
            # 대시보드의 실행 점(dot) 표시와 같은 형식
            "runs": [{"t": x["t"], "c": "success" if x["state"] in _DEPLOY_OK
                      else "failure" if x["state"] in _DEPLOY_FAIL else x["state"], "url": x["url"]}
                     for x in reversed(items)],
            "consecutive_failures": fails,
            "last_success": last_ok["t"] if last_ok else None,
        }
        if fails:
            summary = f"최근 {fails}회 연속 실패"
        else:
            summary = f"최근 배포 성공 ({items[0]['sha']}, {_hours_since(items[0]['t']) / 24:.0f}일 전)"
        return Outcome.of(summary, findings, value)


def _error_hint(ctx: Context, gh: str, repo: str, run_id: int) -> str:
    """실패한 실행 로그에서 마지막 오류 줄을 뽑는다. 실행 ID별로 캐시한다."""
    key = f"ghhint:{repo}:{run_id}"
    cached = ctx.store.get_kv(key)
    if cached is not None:
        return cached
    r = proc.run([gh, "run", "view", str(run_id), "-R", repo, "--log-failed"], timeout=60)
    msgs = []
    for ln in r.out.splitlines():
        msg = _TS_PREFIX.sub("", ln.split("\t")[-1]).strip()
        if (_ERR.search(msg) and "Process completed with exit code" not in msg
                and not msg.startswith(("raise ", "File \"", "Traceback"))):
            msgs.append(msg)
    hint = msgs[-1][:400] if msgs else ""
    if r.ok:
        ctx.store.set_kv(key, hint)
    return hint


def _hours_since(iso: str) -> float:
    return (datetime.now(timezone.utc) - parse_iso(iso)).total_seconds() / 3600


def expected_date(now: datetime, due: str) -> date:
    """due(HH:MM)가 지났으면 오늘, 아니면 어제 날짜의 기록이 있어야 한다."""
    hh, mm = (int(x) for x in due.split(":"))
    return now.date() if now.time() >= dtime(hh, mm) else now.date() - timedelta(days=1)


def dig(data, path: str):
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


class HttpJson(Check):
    light = True

    def __init__(self, spec: HttpJsonCheck):
        self.spec = spec
        self.id = f"http:{spec.name}"
        self.label = f"헬스 {spec.name}"

    def run(self, ctx: Context) -> Outcome:
        spec = self.spec
        try:
            url = secrets.expand(spec.url)
        except secrets.MissingSecret as e:
            return Outcome("skipped", f"시크릿 {e.name} 미설정", findings=[Finding(
                "secret_missing", "warning", f"헬스체크 설정 필요: 시크릿 {e.name} 없음",
                f"터미널에서 `pm secret set {e.name}`으로 값을 넣으면 다음 스캔부터 확인한다.")])

        # URL에는 비밀값이 들어 있을 수 있으므로 결과·Finding에 남기지 않는다
        try:
            resp = ctx.http.get(url, timeout=20)
        except httpx.HTTPError as e:
            return Outcome.of("응답 없음", [Finding("unreachable", "critical",
                                                 f"{spec.name}: 응답 없음 ({type(e).__name__})")])
        latency = int(resp.elapsed.total_seconds() * 1000)
        findings: list[Finding] = []
        value: dict = {"status": resp.status_code, "latency_ms": latency}
        if resp.status_code != spec.expect_status:
            findings.append(Finding("status", "critical", f"{spec.name}: HTTP {resp.status_code} 응답",
                                    resp.text[:300]))
            return Outcome.of(f"HTTP {resp.status_code}", findings, value)

        summary = f"HTTP {resp.status_code} · {latency}ms"
        if spec.field:
            try:
                got = dig(resp.json(), spec.field)
            except ValueError:
                got = None
            value["field"] = got
            if spec.daily_due:
                now = datetime.now(ZoneInfo(spec.tz))
                exp = expected_date(now, spec.daily_due)
                got_date = str(got)[:10] if got else None
                summary += f" · {spec.field}={got_date or '없음'}"
                if not got_date or got_date < exp.isoformat():
                    findings.append(Finding(
                        "stale", "critical",
                        f"{spec.name}: {exp:%m-%d} 기록 없음 (마지막 {got_date or '없음'})",
                        f"{spec.tz} {spec.daily_due} 이후에는 {exp.isoformat()} 기록이 있어야 한다.",
                    ))
            if spec.max_age_hours:
                try:
                    age = _hours_since(str(got))
                except (ValueError, TypeError):
                    age = None
                if age is None or age > spec.max_age_hours:
                    findings.append(Finding(
                        "stale_age", "critical",
                        f"{spec.name}: {spec.field}가 {spec.max_age_hours:.0f}시간 넘게 갱신되지 않음",
                        f"값: {got}",
                    ))
        return Outcome.of(summary, findings, value)
