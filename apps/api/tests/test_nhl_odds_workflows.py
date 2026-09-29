"""Scheduled odds workflows must keep NHL collection enabled."""

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("workflow", "endpoint"),
    [
        ("fetch-odds.yml", "/api/nhl/cron/fetch-odds\""),
        ("fetch-tomorrow-odds.yml", "/api/nhl/cron/fetch-odds?offset=1\""),
    ],
)
def test_scheduled_workflows_collect_nhl_odds(workflow: str, endpoint: str):
    contents = (REPO_ROOT / ".github" / "workflows" / workflow).read_text()

    assert endpoint in contents
