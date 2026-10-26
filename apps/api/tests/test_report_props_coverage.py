from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.error import URLError

import pytest

spec = importlib.util.spec_from_file_location(
    "report_props_coverage",
    Path(__file__).resolve().parents[3] / "scripts/report_props_coverage.py",
)
report_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_script)

MARKETS = ("player_points", "player_points_+_rebounds")
REPORT = {
    "success": True, "game_date": "2026-10-24", "schedule_ok": True,
    "games": 2, "cells_expected": 8, "cells_covered": 5,
    "matrix": {"draftkings": dict.fromkeys(MARKETS, 2), "fanduel": {"player_points": 1, "player_points_+_rebounds": 0}},
    "game_gaps": [{"matchup_key": "NYK@BOS", "starts_at": "2026-10-25T00:00:00+00:00",
                   "missing": ["fanduel player_points", "fanduel player_points_+_rebounds"],
                   "unchecked_only": [], "failed_rows": 0},
                  {"matchup_key": "MIA@CHA", "starts_at": "2026-10-24T23:00:00+00:00",
                   "missing": [], "unchecked_only": ["fanduel player_points_+_rebounds"], "failed_rows": 2}],
    "unexpected_matchups": [],
}
ENV = {"API_URL": "http://unused", "CRON_SECRET": "secret"}


def test_summary_has_matrix_and_gaps_and_one_warning(tmp_path, capsys):
    summary = tmp_path / "summary.md"
    assert report_script.main({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, get=lambda *a: REPORT) == 0
    text = summary.read_text()
    assert "5 of 8 book × market cells" in text
    assert "| player_points | 2/2 | 1/2 |" in text
    assert "| player_points_+_rebounds | 2/2 | 0/2 |" in text
    assert "- NYK@BOS (8:00 PM ET): no usable close: fanduel player_points, fanduel player_points_+_rebounds" in text
    assert "- MIA@CHA (7:00 PM ET): unchecked combos only: fanduel player_points_+_rebounds; 2 failed combo rows" in text
    assert capsys.readouterr().out.count("::warning::") == 1


def test_full_coverage_has_no_warning(capsys):
    full = {**REPORT, "game_gaps": [], "cells_covered": 8}
    assert report_script.main(ENV, get=lambda *a: full) == 0
    assert "::warning::" not in capsys.readouterr().out


def test_unexpected_matchups_warn(capsys):
    report = {**REPORT, "game_gaps": [], "unexpected_matchups": ["GSW@PHX"]}
    assert report_script.main(ENV, get=lambda *a: report) == 0
    out = capsys.readouterr().out
    assert "not on ESPN's played slate: GSW@PHX" in out and "::warning::" in out


def test_no_games_is_one_line():
    lines, warning = report_script.render({**REPORT, "games": 0, "matrix": {}, "game_gaps": []})
    assert lines == ["NBA props close coverage, 2026-10-24: no games."] and warning is None


@pytest.mark.parametrize("response", [
    {**REPORT, "success": False, "error": "NBA schedule unavailable"},
    {**REPORT, "matrix": {}},
    ["not", "an", "object"],
])
def test_a_report_that_cannot_be_produced_fails(response, capsys):
    assert report_script.main(ENV, get=lambda *a: response) == 1
    assert "::error::" in capsys.readouterr().out


def test_request_failure_fails_after_bounded_retries(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(args)
        raise URLError("offline")

    monkeypatch.setattr(report_script, "urlopen", fail)
    monkeypatch.setattr(report_script.time, "sleep", lambda _: None)
    assert report_script.main(ENV) == 1
    assert len(attempts) == report_script.ATTEMPTS
