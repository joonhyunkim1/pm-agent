from datetime import date, datetime
from zoneinfo import ZoneInfo

from pm.checks.base import Finding, Outcome, _parse_porcelain
from pm.checks.deps import bump_kind
from pm.checks.llm_models import find_models
from pm.checks.ops import consecutive_failures, dig, expected_date
from pm.manifest import Manifest

KST = ZoneInfo("Asia/Seoul")


def test_expected_date_before_and_after_due():
    assert expected_date(datetime(2026, 9, 23, 8, 59, tzinfo=KST), "09:00") == date(2026, 9, 22)
    assert expected_date(datetime(2026, 9, 23, 9, 0, tzinfo=KST), "09:00") == date(2026, 9, 23)


def test_consecutive_deploy_failures():
    assert consecutive_failures(["failure", "error", "success", "failure"]) == 2
    assert consecutive_failures(["in_progress", "failure", "inactive"]) == 1
    assert consecutive_failures(["success", "failure"]) == 0
    assert consecutive_failures(["failure"] * 5) == 5
    assert consecutive_failures([]) == 0


def test_dig():
    assert dig({"a": {"b": [1, {"c": 2}]}}, "a.b.1.c") == 2
    assert dig({"a": 1}, "a.b") is None


def test_outcome_status_from_findings():
    assert Outcome.of("", []).status == "ok"
    assert Outcome.of("", [Finding("a", "info", "")]).status == "ok"
    assert Outcome.of("", [Finding("a", "warning", "")]).status == "warn"
    assert Outcome.of("", [Finding("a", "warning", ""), Finding("b", "critical", "")]).status == "fail"


def test_bump_kind():
    assert bump_kind("15.5.3", "16.0.1") == "major"
    assert bump_kind("1.2.3", "1.3.0") == "minor"
    assert bump_kind("1.2.3", "1.2.4") == "patch"
    assert bump_kind("1.2.3", "1.2.3") is None
    assert bump_kind(None, "1.0.0") is None


def test_find_models():
    code = '''
client.responses.create(model="gpt-5.4-mini", input=x)
EMBED = 'text-embedding-3-small'
label = "o3"
not_a_model = "go3"
'''
    assert find_models(code) == {"gpt-5.4-mini", "text-embedding-3-small", "o3"}
    assert find_models("OPENAI_MODEL=gpt-4.1-mini") == {"gpt-4.1-mini"}
    assert find_models("  model: claude-sonnet-5") == {"claude-sonnet-5"}


def test_parse_porcelain_skips_rename_source():
    out = " M a.py\0R  new.py\0old.py\0?? dir/\0"
    assert _parse_porcelain(out) == ["a.py", "new.py", "dir/"]


def test_verify_string_is_coerced_and_l3_needs_v2():
    m = Manifest(id="x", name="x", path="~/x", autonomy="L3", verify={"test": "pytest -q"})
    assert m.verify["test"].run == "pytest -q"
    assert m.verification_level == "V1"
    assert m.effective_autonomy == "L2"
    m = Manifest.model_validate({**m.model_dump(), "health": [{"type": "gh_workflow", "workflow": "a.yml"}]})
    assert m.verification_level == "V2"
    assert m.effective_autonomy == "L3"


def test_only_ops_checks_are_light():
    from pm.checks import build_checks

    m = Manifest.model_validate({
        "id": "x", "name": "x", "path": "~/x", "stack": ["node", "python"],
        "verify": {"test": "pytest"},
        "health": [{"type": "gh_workflow", "workflow": "a.yml"},
                   {"type": "http_json", "name": "h", "url": "https://example.com"},
                   {"type": "gh_deployment"}],
    })
    light = sorted(c.id for c in build_checks(m) if c.light)
    assert light == ["deploy:Production", "gh:a.yml", "http:h"]


def test_tracked_secret_files():
    from pm.checks.hygiene import tracked_secret_files

    files = [".env", ".env.example", "app/.env.local", "src/main.py", "certs/server.pem", "config/.env.sample"]
    assert tracked_secret_files(files) == [".env", "app/.env.local", "certs/server.pem"]


def test_vercelignore_rules(tmp_path):
    from pm.checks.hygiene import ignores_env, vercel_findings

    assert vercel_findings(tmp_path) == []  # Vercel 연결 없음
    (tmp_path / ".vercel").mkdir()
    assert [f.key for f in vercel_findings(tmp_path)] == ["vercelignore_missing"]
    (tmp_path / ".vercelignore").write_text("/exports\nnode_modules\n")
    assert [f.key for f in vercel_findings(tmp_path)] == ["vercelignore_no_env"]
    (tmp_path / ".vercelignore").write_text("# 설명\n.env*\n/exports\n")
    assert vercel_findings(tmp_path) == []
    assert ignores_env("/.env") and ignores_env("*.env") and not ignores_env("!.env\n.env.local")


def test_secret_format_catches_pasted_key_name():
    from pm.secrets import format_problem

    assert format_problem("OPENAI_API_KEY", "pm-manager key") is not None
    assert format_problem("OPENAI_API_KEY", "sk-proj-" + "a" * 60) is None
    assert format_problem("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35) is None
    assert format_problem("TELEGRAM_CHAT_ID", "abc") is not None
    assert format_problem("DR_SITE_URL", "https://x.vercel.app") is None  # 형식 규칙 없는 이름
