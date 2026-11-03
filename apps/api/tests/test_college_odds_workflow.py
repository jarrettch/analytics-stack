"""College collection failures must stay visible without skipping later calls."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/collect_espn_odds.sh"


def test_college_collections_are_individually_guarded_in_one_job():
    workflow = (ROOT / ".github/workflows/fetch-ncaaf-odds.yml").read_text()
    assert workflow.startswith("name: Fetch College Odds\n")
    assert "matrix:" not in workflow
    assert "actions/checkout@" in workflow
    steps = re.split(r"^      - ", workflow, flags=re.M)[1:]
    collect_steps = [step for step in steps if "scripts/collect_espn_odds.sh" in step]
    assert len(collect_steps) == 3
    for step, args in zip(collect_steps, ["ncaaf 0", "ncaab 0", "ncaab 1"]):
        assert "if: ${{ !cancelled() }}" in step
        assert f"run: scripts/collect_espn_odds.sh {args}" in step
    assert workflow.index('"$API_URL/health"') < workflow.index("scripts/collect_espn_odds.sh")


@pytest.mark.skipif(shutil.which("jq") is None, reason="collector script requires jq (installed on GitHub runners)")
@pytest.mark.parametrize("response,curl_status,expected", [
    ('{"success":true,"pregame_snapshot_candidates":2,"snapshot_persistence_ok":true}', 0, 0),
    ('{"success":true,"pregame_snapshot_candidates":0,"snapshot_persistence_ok":false}', 0, 0),
    ('{"success":false}', 0, 1),
    ('{"success":true,"pregame_snapshot_candidates":2,"snapshot_persistence_ok":false}', 0, 1),
    ('{"success":true,"pregame_snapshot_candidates":2}', 0, 1),
    ('not json', 0, 1),
    ('', 22, 22),
])
def test_collect_script_preserves_http_success_and_snapshot_checks(tmp_path, response, curl_status, expected):
    # Execute the real script and jq; stub only curl so no server or secrets
    # are needed and HTTP/transport errors are deterministic.
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$@" > "$CURL_ARGS"\n'
        'printf "%s" "$CURL_RESPONSE"\n'
        'exit "$CURL_STATUS"\n'
    )
    fake_curl.chmod(0o755)
    args_file = tmp_path / "args"
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
           "API_URL": "https://collector.test", "CRON_SECRET": "test-only",
           "CURL_ARGS": str(args_file), "CURL_RESPONSE": response, "CURL_STATUS": str(curl_status)}
    result = subprocess.run(["bash", str(SCRIPT), "ncaab", "1"], env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stdout + result.stderr
    assert args_file.read_text().splitlines() == [
        "--retry", "4", "--retry-delay", "15", "--retry-max-time", "180",
        "--silent", "--show-error", "--fail", "--max-time", "120",
        "--request", "POST", "--header", "Authorization: Bearer test-only",
        "https://collector.test/api/ncaab/cron/fetch-odds?offset=1",
    ]
