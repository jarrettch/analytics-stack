import { describe, expect, it } from "vitest";
import type { NCAABMatchupData, NCAAFMatchupData, Sport } from "../types/sports-types";
import {
  type PrepGame,
  createPrepSelection,
  easternDateKey,
  formatGame,
  formatGameStatus,
  isOnPrepSlate,
  normalizeGame,
  prepGroupState,
  selectVisiblePrepGames,
  setPrepRankedOnly,
  toSlateText,
  visiblePrepGames,
} from "./slatePrep";

function game(key: string, overrides: Partial<PrepGame> = {}): PrepGame {
  return {
    key, sport: "ncaab", date: "2026-11-09", gameStatus: 1,
    status: "7:00 PM ET", time: "7:00 PM ET", away: "Army", home: "Duke",
    homeRank: 5, homeSpread: -7.5, isPreseason: false, ...overrides,
  };
}

function college(sport: "ncaab" | "ncaaf"): NCAABMatchupData | NCAAFMatchupData {
  const team = {
    name: "Hawai'i", abbreviation: "HAW", record: "0-0", winPct: 0,
    conference: "Big West", conferenceId: "4", streak: "N/A",
    predictedScore: null, winProbability: null, score: 0,
    pointsFor: null, pointsAgainst: null, rank: null,
    yardsPG: null, yardsAllowedPG: null, turnoverDiff: null,
  };
  const base = {
    gameId: "hawaii", date: "2026-11-10", slateDate: "2026-11-09",
    time: "1:00 AM ET", status: "1:00 AM ET", gameStatus: 1,
    period: 0, gameClock: null, venue: "Hawai'i", broadcast: "N/A",
    spreadLine: "N/A", model: "MARKET (DK)", overUnder: 180,
    seasonType: "regular-season", statsSeason: 2027,
    away: { ...team, name: "East Texas A&M", abbreviation: "ETAM" }, home: team,
  };
  return sport === "ncaaf"
    ? { ...base, sport, week: 11, weekLabel: "WK11" }
    : { ...base, sport };
}

describe("prep slate date", () => {
  it.each(["ncaab", "ncaaf"] as const)("keeps %s's post-midnight tip on the Nov 9 slate", (sport) => {
    const matchup = college(sport);
    const normalized = normalizeGame(matchup);
    expect(normalized.date).toBe("2026-11-10");
    expect(normalized.slateDate).toBe("2026-11-09");
    for (const now of ["2026-11-09T23:00:00-05:00", "2026-11-10T01:00:00-05:00"]) {
      const slate = easternDateKey(new Date(now));
      expect(slate).toBe("2026-11-09");
      expect(isOnPrepSlate(normalized, slate)).toBe(true);
    }
    expect(isOnPrepSlate(normalized, easternDateKey(new Date("2026-11-10T06:00:00-05:00")))).toBe(false);
  });

  it("falls back to date, excludes finals, and preserves the NFL week exception", () => {
    const daily = game("daily", { sport: "nba" });
    expect(isOnPrepSlate(daily, "2026-11-09")).toBe(true);
    expect(isOnPrepSlate(daily, "2026-11-10")).toBe(false);
    expect(isOnPrepSlate({ ...daily, gameStatus: 2 }, daily.date)).toBe(true);
    expect(isOnPrepSlate({ ...daily, gameStatus: 3 }, daily.date)).toBe(false);
    expect(isOnPrepSlate({ ...daily, sport: "nfl" }, "2026-11-10")).toBe(true);
    expect(isOnPrepSlate({ ...daily, sport: "nfl", gameStatus: 3 }, "2026-11-10")).toBe(false);
  });
});

describe("prep visibility and selection", () => {
  const ranked = game("ranked");
  const awayRanked = game("away-ranked", { awayRank: 12, homeRank: null });
  const unranked = game("unranked", { away: "Unranked away", homeRank: null });
  const unpriced = game("unpriced", { homeSpread: null });
  const games = [ranked, awayRanked, unranked, unpriced];

  it("lists and auto-selects only priced ranked NCAAB games by default", () => {
    const initial = createPrepSelection();
    const added = selectVisiblePrepGames(initial, games, true);
    expect(visiblePrepGames(games, added.rankedOnly)).toEqual([ranked, awayRanked]);
    expect([...added.selectedGames]).toEqual([ranked.key, awayRanked.key]);
    expect(initial.selectedGames.size).toBe(0);
    expect(prepGroupState("ncaab", games, added).allSelected).toBe(true);
  });

  it("reveals unranked games unselected and deselects them when ranked-only returns", () => {
    let selection = selectVisiblePrepGames(createPrepSelection(), games, true);
    selection = setPrepRankedOnly(selection, "ncaab", false, games);
    expect(visiblePrepGames(games, selection.rankedOnly)).toEqual([ranked, awayRanked, unranked]);
    expect(selection.selectedGames.has(unranked.key)).toBe(false);
    expect(prepGroupState("ncaab", games, selection).allSelected).toBe(false);
    selection = selectVisiblePrepGames(selection, games, true);
    expect(selection.selectedGames.has(unranked.key)).toBe(true);
    selection = setPrepRankedOnly(selection, "ncaab", true, games);
    expect(selection.selectedGames.has(unranked.key)).toBe(false);
    selection = setPrepRankedOnly(selection, "ncaab", false, games);
    expect(selection.selectedGames.has(unranked.key)).toBe(false);
  });

  it("selects and deselects only visible games, leaving other groups alone", () => {
    const other = game("football", { sport: "ncaaf", homeRank: null });
    const initial = selectVisiblePrepGames(createPrepSelection(), [other], true);
    const all = selectVisiblePrepGames(initial, games, true);
    const none = selectVisiblePrepGames(all, games, false);
    expect([...all.selectedGames]).toEqual([other.key, ranked.key, awayRanked.key]);
    expect([...none.selectedGames]).toEqual([other.key]);
    expect(prepGroupState("ncaab", games, none).allSelected).toBe(false);
  });

  it("uses the current ranked preference when a fetch finishes", () => {
    let selection = setPrepRankedOnly(createPrepSelection(), "ncaab", false, []);
    selection = setPrepRankedOnly(selection, "ncaab", true, []);
    selection = selectVisiblePrepGames(selection, games, true);
    expect(selection.selectedGames.has(unranked.key)).toBe(false);
  });

  it("never copies hidden games even if a stale selection contains their keys", () => {
    const selection = { ...createPrepSelection(), selectedGames: new Set(games.map((g) => g.key)) };
    const text = toSlateText(["ncaab"], selection, { ncaab: { games } });
    expect(text).toBe("NCAAB\nArmy @ #5 Duke — Spread +7.5/-7.5\n#12 Army @ Duke — Spread +7.5/-7.5");
    // Visible but unchecked games must also be excluded.
    selection.selectedGames.delete(ranked.key);
    expect(toSlateText(["ncaab"], selection, { ncaab: { games } })).not.toContain("#5 Duke");
  });

  it.each([{ slate: [unranked] }, { slate: [] }])("shows the no-ranked state with no selections: $slate", ({ slate }) => {
    const selection = selectVisiblePrepGames(createPrepSelection(), slate, true);
    const group = prepGroupState("ncaab", slate, selection);
    expect(group.emptyMessage).toBe("No ranked teams today");
    expect(group.games).toEqual([]);
    expect(group.allSelected).toBe(false);
    expect(toSlateText(["ncaab"], selection, { ncaab: { games: slate } })).toBe("");
    const revealed = setPrepRankedOnly(selection, "ncaab", false, slate);
    expect(prepGroupState("ncaab", slate, revealed).games).toEqual(slate);
    expect(revealed.selectedGames.size).toBe(0);
  });

  it.each(["ncaaf", "nba", "nfl", "mlb", "wnba", "nhl"] satisfies Sport[])(
    "keeps unranked/unpriced %s games visible and selected across NCAAB toggles", (sport) => {
      const other = game(sport, { sport, homeRank: null, homeSpread: null });
      const mixed = [...games, other];
      let selection = selectVisiblePrepGames(createPrepSelection(), mixed, true);
      for (const enabled of [false, true]) {
        selection = setPrepRankedOnly(selection, "ncaab", enabled, mixed);
        expect(prepGroupState(sport, [other], selection).selected).toEqual([other]);
        expect(toSlateText([sport], selection, { [sport]: { games: [other] } })).toContain("Army");
      }
    },
  );
});

describe("prep formatting", () => {
  it("carries college ranks but prefixes only basketball teams", () => {
    for (const sport of ["ncaab", "ncaaf"] as const) {
      const matchup = college(sport);
      matchup.away.rank = 5;
      matchup.home.rank = 12;
      const normalized = normalizeGame(matchup);
      expect([normalized.awayRank, normalized.homeRank]).toEqual([5, 12]);
      expect(formatGame(normalized)).toBe(sport === "ncaab"
        ? "#5 East Texas A&M @ #12 Hawai'i — O/U 180"
        : "East Texas A&M @ Hawai'i — O/U 180");
    }
  });

  it.each(["2H 5:12", "Halftime", "OT 3:21"])("preserves NCAAB server status %s", (status) => {
    const matchup = { ...college("ncaab"), gameStatus: 2, period: 2, gameClock: "5:12", status };
    expect(formatGameStatus(normalizeGame(matchup))).toBe(status);
  });

  it("preserves MLB pitchers, NBA tip times, NFL preseason headings, and odds output", () => {
    expect(formatGame(game("mlb", { sport: "mlb", awayPitcher: " Pitcher ", homePitcher: "TBD" })))
      .toBe("Army (Pitcher) vs Duke (TBD) — RL +7.5/-7.5");
    expect(formatGameStatus(game("nba", { sport: "nba", time: "19:30", status: "Scheduled" })))
      .toBe("7:30 PM ET · Scheduled");
    const nfl = game("nfl", {
      sport: "nfl", isPreseason: true, awayMoneyline: 120, homeMoneyline: -140,
      spreadOddsAway: -105, spreadOddsHome: -115, overUnder: 45.5, overOdds: -110, underOdds: -110,
    });
    const selection = selectVisiblePrepGames(createPrepSelection(), [nfl], true);
    expect(toSlateText(["nfl"], selection, { nfl: { games: [nfl] } })).toBe(
      "NFL — PRESEASON\nArmy @ Duke — Spread +7.5/-7.5 (-105/-115), ML +120/-140, O/U 45.5 (o-110/u-110)",
    );
  });
});
