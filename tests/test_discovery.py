import json
import subprocess

import pytest

from pm import discovery
from pm.config import Config


@pytest.mark.parametrize("expr, hours", [
    ("0 10 * * *", 24),
    ("0 11,16,22,3 * * *", 8),
    ("*/10 * * * *", 10 / 60),
    ("30 * * * *", 1),
    ("0 */6 * * *", 6),
    ("0 9 * * 1", 24 * 7),
    ("0 9 * * 1,4", 24 * 4),
])
def test_cron_period_hours(expr, hours):
    assert discovery.cron_period_hours(expr) == pytest.approx(hours)


def test_slug_falls_back_for_non_ascii():
    assert discovery.slug("multilang-chat") == "multilang-chat"
    assert discovery.slug("temp_test") == "temp-test"
    assert discovery.slug("자기소개서").startswith("p-")


def test_discover_node_project_with_scheduled_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "deployment_checks", lambda repo: [])  # GitHub API 호출 없이
    proj = tmp_path / "Projects" / "demo"
    (proj / ".github" / "workflows").mkdir(parents=True)
    (proj / "package.json").write_text(json.dumps(
        {"scripts": {"test": 'echo "Error: no test specified" && exit 1', "build": "next build"}}))
    (proj / ".github" / "workflows" / "cron.yml").write_text(
        'on:\n  schedule:\n    - cron: "0 10 * * *"\n  workflow_dispatch: {}\n')
    (proj / "data").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:me/demo.git"], cwd=proj, check=True)

    m = discovery.discover(proj, Config(default_autonomy="L3"))
    assert m.repo == "me/demo"
    assert m.stack == ["node"]
    assert list(m.verify) == ["build"]  # npm 기본 test 스크립트는 무시
    assert m.health[0].workflow == "cron.yml"
    assert m.health[0].max_age_hours == pytest.approx(24 * 1.5 + 3)
    assert m.stage == "deployed"
    assert "data/" in m.exclude and "data/" in m.protected_paths
    # 테스트가 없으니 V1 → L3를 원해도 실제로는 L2
    assert m.verification_level == "V1"
    assert m.effective_autonomy == "L2"


def test_non_git_folder_is_observe_only(tmp_path):
    proj = tmp_path / "scratch"
    proj.mkdir()
    m = discovery.discover(proj, Config())
    assert m.autonomy == "L0"
    assert m.repo is None
