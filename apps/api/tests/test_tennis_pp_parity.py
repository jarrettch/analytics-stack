"""The port must reproduce the scripts it replaces.

Fixtures are real inputs (a PrizePicks board reduced to the fields the page uploads, and the
SharpAPI pull saved beside it) with the output of sports/projections/scripts/tennis_fs.py and
tennis_games.py on them. Where the port differs on purpose (specs/tennis_fs_upload.md, "Intended
differences"), the test says so rather than comparing.
"""
from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tennis_pp.pricing import price_board

FIXTURES = Path(__file__).parent / "fixtures" / "tennis_pp"
BEFORE_ANY_MATCH = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _load(tag: str):
    board = json.loads((FIXTURES / f"board_{tag}.json").read_text())
    snapshot = json.loads((FIXTURES / f"snapshot_{tag}.json").read_text())
    results = price_board(board, snapshot, now=BEFORE_ANY_MATCH)
    by_key = {(r["stat"], r["player"], r["odds_type"], float(r["line"])): r for r in results}
    return results, by_key


def _script(tag: str, name: str) -> list[dict]:
    with (FIXTURES / f"script_{name}_{tag}.csv").open() as handle:
        return list(csv.DictReader(handle))


class ParityMixin:
    tag: str
    # Players the scripts couldn't match to a book event but the port can: the scripts match
    # names on suffix only, the port also on prefix ("Ariana Geerlings Martinez" on PrizePicks,
    # "Ariana Geerlings" at the book).
    newly_matched: set[str] = set()

    def _unpriced_by_script(self, ours: dict, row: dict, where: str, seen: set[str]) -> None:
        if ours["unpriced_reason"] is None:
            seen.add(row["player"])
        else:
            self.assertEqual(ours["unpriced_reason"], "no_book_line", where)

    @classmethod
    def setUpClass(cls):
        cls.results, cls.by_key = _load(cls.tag)

    def test_fantasy_score_matches_the_script(self):
        script = _script(self.tag, "fs")
        priced, newly_matched = 0, set()
        for row in script:
            ours = self.by_key[("Fantasy Score", row["player"], row["pp_type"], float(row["pp_line"]))]
            where = f"{row['player']} {row['pp_type']} {row['pp_line']}"
            if row.get("note"):
                self._unpriced_by_script(ours, row, where, newly_matched)
                continue
            priced += 1
            for theirs, mine in (("p_over", "p_over"), ("p_under", "p_under"), ("push", "push"),
                                 ("p_win", "p_win"), ("model_mean", "model_mean"),
                                 ("model_median", "model_median"), ("p_side_bias", "p_side_bias"),
                                 ("total", "total"), ("spread", "spread")):
                self.assertAlmostEqual(float(row[theirs]), ours[mine], places=3, msg=f"{where} {mine}")
            self.assertEqual(row["aces_src"], ours["aces_src"], where)
            self.assertEqual(row["fit"], ours["fit"], where)
            self.assertEqual(row["tour"], ours["tour"], where)
            self.assertEqual(row["bias_check"] == "FAIL", "fails_bias_check" in ours["flags"], where)
        self.assertGreater(priced, 10)
        self.assertLessEqual(newly_matched, self.newly_matched)
        self.assertEqual(len(script), sum(r["stat"] == "Fantasy Score" for r in self.results))

    def test_games_and_games_won_match_the_script(self):
        script = _script(self.tag, "games")
        priced, newly_matched = 0, set()
        for row in script:
            ours = self.by_key[(row["stat"], row["player"], row["pp_type"], float(row["pp_line"]))]
            where = f"{row['stat']} {row['player']} {row['pp_type']} {row['pp_line']}"
            if row.get("note"):
                self._unpriced_by_script(ours, row, where, newly_matched)
                continue
            priced += 1
            for field in ("p_over", "p_under", "push", "p_win", "model_mean"):
                self.assertAlmostEqual(float(row[field]), ours[field], places=3, msg=f"{where} {field}")
            self.assertEqual(int(row["fav_ml"]), ours["fav_ml"], where)
            theirs = set(row["flags"].split())
            fit_labels = {flag for flag in theirs if flag.startswith("mix")}
            # The script writes the mixture label into its flags; the port keeps it in `fit`.
            self.assertEqual(fit_labels, {ours["fit"].replace(" ", "")} if ours["fit"].startswith("mix") else set(), where)
            # over_only and fit_miss are new in the port.
            self.assertEqual(theirs - fit_labels, set(ours["flags"]) - {"over_only", "fit_miss"}, where)
            if row["dk_gw_p_over"]:
                self.assertAlmostEqual(float(row["dk_gw_p_over"]), ours["dk_gw_p_over"], places=3, msg=where)
            else:
                self.assertNotIn("dk_gw_p_over", ours, where)
            if row["stat"] == "Total Games":
                shown = (f"{ours['dk_p_over']:.3f}" if "dk_p_over" in ours
                         else f"{ours['dk_p_over_lo']:.3f}/{ours['dk_p_over_hi']:.3f}" if "dk_p_over_lo" in ours else "")
                self.assertEqual(row["dk_p_over"], shown, where)
        self.assertGreater(priced, 10)
        self.assertLessEqual(newly_matched, self.newly_matched)
        self.assertEqual(len(script), sum(r["stat"] != "Fantasy Score" for r in self.results))


class Board1115ParityTest(ParityMixin, unittest.TestCase):
    """ATP Finals week: mostly ATP, FD and DK ace ladders, PrizePicks standard ace lines."""
    tag = "11-15"
    newly_matched = {"Luisina Giovannini", "Ariana Geerlings Martinez"}


class Board1105ParityTest(ParityMixin, unittest.TestCase):
    """A larger board with many WTA matches, where the iid fit can't reach the total."""
    tag = "11-05"


if __name__ == "__main__":
    unittest.main()
