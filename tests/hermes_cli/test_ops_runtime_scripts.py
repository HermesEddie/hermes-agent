from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "ops" / "bootstrap_runtime.sh"
CREATE_RELEASE_SCRIPT = REPO_ROOT / "scripts" / "ops" / "create_release.sh"


def test_bootstrap_runtime_creates_decoupled_layout(tmp_path):
    srv_root = tmp_path / "srv" / "hermes-agent"
    etc_root = tmp_path / "etc" / "hermes-agent"

    result = subprocess.run(
        [
            "bash",
            str(BOOTSTRAP_SCRIPT),
            "--srv-root",
            str(srv_root),
            "--etc-root",
            str(etc_root),
            "--network",
            "tower-aps-network-production",
            "--runtime-model",
            "gpt-5.3-codex",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    env_file = etc_root / "production" / "hermes-agent.env"
    compose_file = srv_root / "production" / "compose.yaml"
    login_script = srv_root / "production" / "login_codex.sh"
    home_dir = srv_root / "production" / "home"

    assert env_file.exists()
    assert compose_file.exists()
    assert login_script.exists()
    assert home_dir.is_dir()

    env_text = env_file.read_text(encoding="utf-8")
    assert "TOWER_AGENT_REVIEW_RUNTIME_MODEL=gpt-5.3-codex" in env_text
    assert "API_SERVER_KEY=REPLACE_ME" in env_text

    compose_text = compose_file.read_text(encoding="utf-8")
    assert str(home_dir) in compose_text
    assert "tower-aps-network-production" in compose_text
    assert "hermes-agent" in compose_text

    login_text = login_script.read_text(encoding="utf-8")
    assert str(env_file) in login_text
    assert str(home_dir) in login_text
    assert "auth" in login_text
    assert "bootstrapped runtime layout" in result.stdout


def test_create_release_dry_run_reports_release_metadata(tmp_path):
    result = subprocess.run(
        [
            "bash",
            str(CREATE_RELEASE_SCRIPT),
            "--release-id",
            "test-release",
            "--output-root",
            str(tmp_path),
            "--target",
            "runtime-core",
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    output = result.stdout
    assert "release_id=test-release" in output
    assert f"release_dir={tmp_path / 'test-release'}" in output
    assert "image_tag=hermes-agent:release-test-release" in output
    assert "target=runtime-core" in output
    assert "platform=linux/amd64" in output
