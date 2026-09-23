"""SQLite 저장소: 스캔 이력, 체크 결과, Finding 수명주기, 이벤트 로그, 아이디어, KV.

Finding은 (프로젝트, 체크, key)의 fingerprint로 식별한다. 같은 문제는 한 번만 알리고,
사라지면 resolved, 다시 나타나면 reopen 한다.
events 테이블은 append-only다. 나중에 비용 원장·감사 기록의 기반이 된다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import paths

SEV_RANK = {"info": 0, "warning": 1, "critical": 2}
ACTIVE = ("open", "acknowledged")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    value_json TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_results_project ON check_results(project_id, check_id, id);
CREATE TABLE IF NOT EXISTS findings (
    fingerprint TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    key TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    url TEXT,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    resolved_at TEXT,
    notified_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_findings_project ON findings(project_id, status);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    project_id TEXT,
    type TEXT NOT NULL,
    payload_json TEXT
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    purpose TEXT NOT NULL,
    project_id TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL,
    error TEXT,
    response_id TEXT,
    duration_ms INTEGER,
    tier TEXT
);
CREATE INDEX IF NOT EXISTS ix_llm_calls_ts ON llm_calls(ts);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    priority INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    decided_at TEXT,
    decided_via TEXT,
    decision_note TEXT,
    tg_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_proposals_status ON proposals(status, project_id);
"""

# 제안 상태: proposed(승인함) · backlog(한도 초과로 대기) · deferred(보류) → approved · rejected
# approved → done은 P2 실행기가 쓴다.
PROPOSAL_OPEN = ("proposed", "backlog", "deferred")
PROPOSAL_DECISIONS = {"approved", "rejected", "deferred", "proposed"}

_SEV_ORDER_SQL = "CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fingerprint(project_id: str, check_id: str, key: str) -> str:
    return hashlib.sha1(f"{project_id}\x1f{check_id}\x1f{key}".encode()).hexdigest()[:16]


class Store:
    def __init__(self, path: Path | None = None):
        path = path or paths.db_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """이미 만들어진 DB에 나중에 추가한 열을 붙인다."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(llm_calls)")}
        if "tier" not in cols:
            with self.db:
                self.db.execute("ALTER TABLE llm_calls ADD COLUMN tier TEXT")

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 스캔 실행 ----
    def start_run(self, trigger: str) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO scan_runs(trigger, started_at) VALUES (?, ?)", (trigger, now_iso())
            )
        self.event("scan_started", trigger=trigger, run_id=cur.lastrowid)
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, summary: dict) -> None:
        with self.db:
            self.db.execute(
                "UPDATE scan_runs SET finished_at=?, status=?, summary_json=? WHERE id=?",
                (now_iso(), status, json.dumps(summary, ensure_ascii=False), run_id),
            )
        self.event("scan_finished", run_id=run_id, status=status)

    def last_finished_run(self, full_only: bool = False) -> dict | None:
        light = " AND trigger NOT LIKE '%-light'" if full_only else ""
        r = self.db.execute(
            f"SELECT * FROM scan_runs WHERE finished_at IS NOT NULL{light} ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return _run_dict(r) if r else None

    def runs(self, limit: int = 30) -> list[dict]:
        rows = self.db.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,))
        return [_run_dict(r) for r in rows]

    # ---- 체크 결과 ----
    def add_result(
        self, run_id: int, project_id: str, check_id: str, label: str, outcome, duration_ms: int
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO check_results(run_id, project_id, check_id, label, status, summary,"
                " value_json, duration_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    project_id,
                    check_id,
                    label,
                    outcome.status,
                    outcome.summary,
                    json.dumps(outcome.value, ensure_ascii=False, default=str),
                    duration_ms,
                    now_iso(),
                ),
            )

    def latest_results(self, project_id: str | None = None) -> list[dict]:
        sql = (
            "SELECT * FROM check_results WHERE id IN "
            "(SELECT MAX(id) FROM check_results GROUP BY project_id, check_id)"
        )
        args: tuple = ()
        if project_id:
            sql += " AND project_id=?"
            args = (project_id,)
        rows = self.db.execute(sql + " ORDER BY project_id, id", args)
        out = []
        for r in rows:
            d = dict(r)
            d["value"] = json.loads(d.pop("value_json") or "{}")
            out.append(d)
        return out

    def prune(self, days: int = 30) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.db:
            # 체크별 최신 결과는 기간과 상관없이 남긴다
            self.db.execute(
                "DELETE FROM check_results WHERE created_at < ? AND id NOT IN "
                "(SELECT MAX(id) FROM check_results GROUP BY project_id, check_id)",
                (cutoff,),
            )

    # ---- Finding ----
    def apply_findings(self, project_id: str, check_id: str, outcome) -> list[str]:
        """체크 결과를 Finding 상태에 반영하고, 새로 알릴 대상의 fingerprint를 돌려준다.

        - 처음 보는 문제 → open
        - 해결됐던 문제가 재발 → open (reopen)
        - 확인(ack)한 문제가 더 심각해짐 → open
        - 이번 결과에 없는 문제 → resolved (체크가 정상 실행된 경우에만)
        """
        now = now_iso()
        rows = {
            r["key"]: r
            for r in self.db.execute(
                "SELECT * FROM findings WHERE project_id=? AND check_id=?", (project_id, check_id)
            )
        }
        fresh: list[str] = []
        seen: set[str] = set()
        with self.db:
            for f in outcome.findings:
                if f.key in seen:
                    continue
                seen.add(f.key)
                fp = fingerprint(project_id, check_id, f.key)
                row = rows.get(f.key)
                if row is None:
                    self.db.execute(
                        "INSERT INTO findings(fingerprint, project_id, check_id, key, severity, title,"
                        " detail, url, status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (fp, project_id, check_id, f.key, f.severity, f.title, f.detail, f.url,
                         "open", now, now),
                    )
                    self._event("finding_opened", project_id, fingerprint=fp, title=f.title,
                                severity=f.severity)
                    fresh.append(fp)
                    continue

                status = row["status"]
                escalated = SEV_RANK[f.severity] > SEV_RANK[row["severity"]]
                notified_at = row["notified_at"]
                if status == "resolved" or (status == "acknowledged" and escalated):
                    self._event("finding_reopened", project_id, fingerprint=fp, title=f.title,
                                severity=f.severity)
                    status, notified_at = "open", None
                    fresh.append(fp)
                elif status == "open" and escalated:
                    notified_at = None
                    fresh.append(fp)
                self.db.execute(
                    "UPDATE findings SET severity=?, title=?, detail=?, url=?, status=?, last_seen=?,"
                    " resolved_at=NULL, notified_at=? WHERE fingerprint=?",
                    (f.severity, f.title, f.detail, f.url, status, now, notified_at, fp),
                )

            if outcome.status in ("ok", "warn", "fail"):
                for key, row in rows.items():
                    if key not in seen and row["status"] in ACTIVE:
                        self.db.execute(
                            "UPDATE findings SET status='resolved', resolved_at=? WHERE fingerprint=?",
                            (now, row["fingerprint"]),
                        )
                        self._event("finding_resolved", project_id,
                                    fingerprint=row["fingerprint"], title=row["title"])
        return fresh

    def findings(
        self, project_id: str | None = None, statuses: tuple[str, ...] | None = None
    ) -> list[dict]:
        sql = "SELECT * FROM findings WHERE 1=1"
        args: list[Any] = []
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        sql += f" ORDER BY {_SEV_ORDER_SQL}, last_seen DESC"
        return [dict(r) for r in self.db.execute(sql, args)]

    def finding(self, fp: str) -> dict | None:
        r = self.db.execute("SELECT * FROM findings WHERE fingerprint=?", (fp,)).fetchone()
        return dict(r) if r else None

    def set_finding_status(self, fp: str, status: str) -> bool:
        row = self.finding(fp)
        if not row or row["status"] == "resolved":
            return False
        with self.db:
            self.db.execute("UPDATE findings SET status=? WHERE fingerprint=?", (status, fp))
            self._event(f"finding_{status}", row["project_id"], fingerprint=fp, title=row["title"])
        return True

    def unnotified(self, severity: str = "critical") -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM findings WHERE status='open' AND notified_at IS NULL AND severity=?"
            " ORDER BY first_seen",
            (severity,),
        )
        return [dict(r) for r in rows]

    def mark_notified(self, fps: list[str]) -> None:
        now = now_iso()
        with self.db:
            self.db.executemany(
                "UPDATE findings SET notified_at=? WHERE fingerprint=?", [(now, fp) for fp in fps]
            )

    def changed_since(self, since: str) -> dict[str, list[dict]]:
        opened = self.db.execute(
            "SELECT * FROM findings WHERE first_seen > ? AND status IN ('open','acknowledged')"
            f" ORDER BY {_SEV_ORDER_SQL}",
            (since,),
        )
        resolved = self.db.execute(
            "SELECT * FROM findings WHERE resolved_at > ? AND status='resolved'", (since,)
        )
        return {"opened": [dict(r) for r in opened], "resolved": [dict(r) for r in resolved]}

    # ---- 이벤트 ----
    def event(self, type_: str, project_id: str | None = None, **payload) -> None:
        with self.db:
            self._event(type_, project_id, **payload)

    def _event(self, type_: str, project_id: str | None, **payload) -> None:
        self.db.execute(
            "INSERT INTO events(ts, project_id, type, payload_json) VALUES (?,?,?,?)",
            (now_iso(), project_id, type_, json.dumps(payload, ensure_ascii=False, default=str)),
        )

    def events(self, project_id: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM events"
        args: tuple = ()
        if project_id:
            sql += " WHERE project_id=?"
            args = (project_id,)
        rows = self.db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json") or "{}")
            out.append(d)
        return out

    # ---- 아이디어 ----
    def ideas(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM ideas WHERE status='active' ORDER BY id DESC")
        return [dict(r) for r in rows]

    def add_idea(self, title: str, note: str = "") -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO ideas(title, note, created_at) VALUES (?,?,?)", (title, note, now_iso())
            )
            self._event("idea_added", None, idea_id=cur.lastrowid, title=title)
        return int(cur.lastrowid)

    def archive_idea(self, idea_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE ideas SET status='archived' WHERE id=?", (idea_id,))
            self._event("idea_archived", None, idea_id=idea_id)

    # ---- LLM 비용 원장 ----
    def add_llm_call(self, *, purpose: str, project_id: str | None, model: str, usage: dict,
                     cost_usd: float, ok: bool, error: str | None = None,
                     response_id: str | None = None, duration_ms: int = 0, tier: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO llm_calls(ts, purpose, project_id, model, input_tokens, cached_tokens,"
                " cache_write_tokens, output_tokens, reasoning_tokens, cost_usd, ok, error, response_id,"
                " duration_ms, tier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), purpose, project_id, model, usage.get("input", 0), usage.get("cached", 0),
                 usage.get("cache_write", 0), usage.get("output", 0), usage.get("reasoning", 0),
                 cost_usd, int(ok), error, response_id, duration_ms, tier),
            )

    def month_spend(self) -> float:
        """이번 달(UTC, OpenAI 청구 기준) LLM 비용 합계."""
        start = datetime.now(timezone.utc).strftime("%Y-%m-01")
        r = self.db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_calls WHERE ts >= ?", (start,))
        return float(r.fetchone()["s"])

    def llm_calls(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # ---- 제안 ----
    def add_proposal(self, pid: str, project_id: str, status: str, data: dict) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO proposals(id, project_id, created_at, status, title, kind, priority, data_json)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (pid, project_id, now_iso(), status, data["title"], data["kind"], data["priority"],
                 json.dumps(data, ensure_ascii=False)),
            )
            self._event("proposal_created", project_id, proposal_id=pid, title=data["title"], status=status)

    def proposals(self, project_id: str | None = None,
                  statuses: tuple[str, ...] | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM proposals WHERE 1=1"
        args: list[Any] = []
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        sql += " ORDER BY created_at DESC, priority LIMIT ?"
        args.append(limit)
        return [_proposal_dict(r) for r in self.db.execute(sql, args)]

    def proposal(self, pid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return _proposal_dict(r) if r else None

    def decide_proposal(self, pid: str, status: str, via: str, note: str = "") -> dict | None:
        """승인·거절·보류. 이미 결정된(approved/rejected/done) 제안은 바꾸지 않는다."""
        if status not in PROPOSAL_DECISIONS:
            raise ValueError(status)
        p = self.proposal(pid)
        if not p or p["status"] not in PROPOSAL_OPEN:
            return None
        with self.db:
            self.db.execute(
                "UPDATE proposals SET status=?, decided_at=?, decided_via=?, decision_note=? WHERE id=?",
                (status, now_iso(), via, note or None, pid),
            )
            self._event(f"proposal_{status}", p["project_id"], proposal_id=pid, title=p["title"], via=via,
                        note=note)
        return self.proposal(pid)

    def set_proposal_message(self, pid: str, message_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE proposals SET tg_message_id=? WHERE id=?", (message_id, pid))

    # ---- KV ----
    def get_kv(self, key: str, default: Any = None) -> Any:
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_kv(self, key: str, value: Any) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO kv(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False, default=str)),
            )


def _proposal_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["data"] = json.loads(d.pop("data_json") or "{}")
    return d


def _run_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["summary"] = json.loads(d.pop("summary_json") or "{}")
    return d
