from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

spec = importlib.util.spec_from_file_location(
    "run_props_collection",
    Path(__file__).resolve().parents[3] / "scripts/run_props_collection.py",
)
driver = importlib.util.module_from_spec(spec)
# dataclasses look the module up by name while the class is being built.
sys.modules[spec.name] = driver
spec.loader.exec_module(driver)

GOOD = {
    "success": True, "schedule_ok": True, "sweep_complete": True, "persistence_ok": True,
    "markets_requested": 8, "markets_completed": 8, "unmapped_teams": [], "rows_rejected": 0,
    "games_selected": 1, "games_persisted": 1, "rows_inserted": 16, "rows_deduplicated": 0,
    "rows_skipped": 0,
}


def gate_env(**changes):
    env = {
        "API_URL": "http://unused", "CRON_SECRET": "secret", "GITHUB_RUN_ID": "123",
        "WINDOW_AT": "2026-10-25T21:37:00Z", "NBA_PHASE": "close",
        "NFL_GATE_OK": "true", "NFL_ELIGIBLE": "true", "NFL_DEADLINE": "2026-10-25T22:15:00Z",
        "NBA_GATE_OK": "true", "NBA_ELIGIBLE": "true", "NBA_DEADLINE": "2026-10-25T21:50:00Z",
    }
    env.update(changes)
    return {k: v for k, v in env.items() if v is not None}


@pytest.fixture
def api():
    """A local API whose per-league behavior each test sets."""
    behavior = {"nfl": ("json", GOOD), "nba": ("json", {**GOOD, "phase": "close"})}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            url = urlsplit(self.path)
            league = url.path.split("/")[2]
            calls.append((league, parse_qs(url.query), self.headers.get("Authorization"), time.monotonic()))
            kind, payload = behavior[league]
            if kind == "json":
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif kind == "status":
                self.send_response(payload)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            elif kind == "text":
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif kind == "trickle":
                # Headers promptly, then a byte at a time: each read is fast,
                # but the response never finishes within the limit.
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                try:
                    for _ in range(1000):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass
            elif kind == "hang":
                time.sleep(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", behavior, calls
    server.shutdown()
    server.server_close()


def run(api, limit=2.0, **env_changes):
    url, _, calls = api
    env = gate_env(API_URL=url, **env_changes)
    return driver.main(env, limit=limit), calls


def test_both_leagues_collected_earliest_deadline_first(api):
    status, calls = run(api)
    assert status == 0
    assert [c[0] for c in calls] == ["nba", "nfl"]
    assert all(c[2] == "Bearer secret" for c in calls)


def test_order_follows_deadlines_not_league(api):
    status, calls = run(api, NFL_DEADLINE="2026-10-25T21:45:00Z")
    assert status == 0 and [c[0] for c in calls] == ["nfl", "nba"]


def test_collection_keys_and_phase(api):
    _, calls = run(api, NBA_PHASE="intraday")
    params = {league: query for league, query, _, _ in calls}
    assert params["nfl"] == {"collection_key": ["123-nfl-props"], "window_at": ["2026-10-25T21:37:00Z"]}
    assert params["nba"] == {"collection_key": ["123-nba-props-intraday"], "phase": ["intraday"],
                             "window_at": ["2026-10-25T21:37:00Z"]}


def test_nothing_eligible_is_success_without_requests(api):
    status, calls = run(api, NFL_ELIGIBLE="false", NBA_ELIGIBLE="false",
                        NFL_DEADLINE=None, NBA_DEADLINE=None)
    assert status == 0 and calls == []


@pytest.mark.parametrize("gate_ok", ["false", None])
def test_one_leagues_gate_failure_still_collects_the_other_and_fails(api, gate_ok):
    status, calls = run(api, NBA_GATE_OK=gate_ok, NBA_ELIGIBLE=None, NBA_DEADLINE=None)
    assert status == 1
    assert [c[0] for c in calls] == ["nfl"]


@pytest.mark.parametrize("kind,payload", [
    ("status", 500), ("status", 502), ("status", 401), ("text", b"not json"),
    ("json", {**GOOD, "success": False}), ("json", {**GOOD, "rows_rejected": 1}),
    ("json", {**GOOD, "unmapped_teams": ["Mystery"]}), ("json", {**GOOD, "markets_completed": 7}),
    ("json", {k: v for k, v in GOOD.items() if k != "persistence_ok"}),
    ("json", {k: v for k, v in GOOD.items() if k != "markets_requested"}),
    ("json", ["not", "an", "object"]),
])
def test_first_league_failure_is_never_retried_and_second_still_runs(api, kind, payload):
    _, behavior, _ = api
    behavior["nba"] = (kind, payload)
    status, calls = run(api)
    assert status == 1
    assert [c[0] for c in calls] == ["nba", "nfl"]


def test_trickling_response_is_cut_at_the_total_limit(api):
    _, behavior, _ = api
    behavior["nba"] = ("trickle", None)
    started = time.monotonic()
    status, calls = run(api, limit=0.6)
    assert status == 1
    assert [c[0] for c in calls] == ["nba", "nfl"]
    # The second league starts right after the first league's limit, not after the trickle ends.
    assert calls[1][3] - started < 1.5


def test_silent_server_is_cut_at_the_total_limit(api):
    _, behavior, _ = api
    behavior["nba"] = ("hang", 5)
    started = time.monotonic()
    status, calls = run(api, limit=0.5)
    assert status == 1 and [c[0] for c in calls] == ["nba", "nfl"]
    assert calls[1][3] - started < 1.5


def test_unreachable_api_fails_both_without_retry():
    env = gate_env(API_URL="http://127.0.0.1:9")
    attempts = []

    def send(*args):
        attempts.append(args)
        return driver.post(*args)

    assert driver.main(env, limit=2.0, send=send) == 1
    assert len(attempts) == 2


def test_no_eligible_game_after_api_recheck_reads_as_noop(api):
    _, behavior, _ = api
    behavior["nfl"] = ("json", {**GOOD, "games_selected": 0})
    status, _ = run(api)
    assert status == 0


def test_step_summary_lists_each_league(api, tmp_path):
    _, behavior, _ = api
    behavior["nba"] = ("json", {**GOOD, "phase": "intraday",
                                "combo_checks": {"draftkings": {"passed": 88, "failed": 0, "unchecked": 206}}})
    summary = tmp_path / "summary.md"
    status, _ = run(api, GITHUB_STEP_SUMMARY=str(summary))
    text = summary.read_text()
    assert status == 0
    assert "NBA intraday: 16 rows inserted" in text
    assert "draftkings 88 passed, 0 failed, 206 unchecked" in text
    assert "NFL close: 16 rows inserted" in text
