import { describe, it, expect } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { MatchupCard } from "./MatchupCard"
import type { MatchupData, NBAMatchupData, NCAAFMatchupData } from "./types/sports-types"

const base = {
  gameId: "1",
  status: "7:00 PM ET",
  date: "2026-09-26",
  time: "7:00 PM ET",
  venue: "Test Arena",
  broadcast: "TV",
  spreadLine: "N/A",
  model: "TEST",
}

const baseTeam = {
  record: "3-0",
  winPct: 1,
  conference: "TEST",
  predictedScore: 0,
  winProbability: null,
  score: 0,
}

function nba(streak: string | undefined): NBAMatchupData {
  const team = (abbreviation: string) => ({
    ...baseTeam,
    name: abbreviation,
    abbreviation,
    streak: streak as string,
    last10: "7-3",
    offRating: 110,
    defRating: 108,
    pace: 99,
  })
  return { ...base, sport: "nba", gameStatus: 1, period: 0, gameClock: "", away: team("AAA"), home: team("BBB") }
}

function ncaaf(streak: string | undefined): NCAAFMatchupData {
  const team = (abbreviation: string) => ({
    ...baseTeam,
    name: abbreviation,
    abbreviation,
    streak: streak as string,
    pointsFor: 30,
    pointsAgainst: 20,
    yardsPG: null,
    yardsAllowedPG: null,
    turnoverDiff: null,
    rank: null,
    conferenceId: "8",
  })
  return {
    ...base, sport: "ncaaf", gameStatus: 1, period: 0, gameClock: null,
    week: 4, weekLabel: "WK4", seasonType: "regular-season", statsSeason: 2026,
    away: team("AAA"), home: team("BBB"),
  }
}

/** The rendered Streak cells, one per team: label through the next stat label. */
function streakCells(matchup: MatchupData): string[] {
  const html = renderToStaticMarkup(<MatchupCard matchup={matchup} />)
  return html.split(">Streak</span>").slice(1).map((rest) => rest.split("uppercase tracking-wider")[0] ?? "")
}

describe.each([
  ["NBATeamStats (NBA/WNBA)", nba],
  ["NFLTeamStats (NFL/NCAAF)", ncaaf],
])("streak in %s", (_name, build) => {
  it("renders a winning streak with the green up arrow", () => {
    const cells = streakCells(build("W3"))
    expect(cells).toHaveLength(2)
    for (const cell of cells) {
      expect(cell).toContain("lucide-arrow-up")
      expect(cell).toContain("text-emerald-400")
      expect(cell).not.toContain("lucide-arrow-down")
      expect(cell).toContain("W3")
    }
  })

  it("renders a losing streak with the red down arrow", () => {
    for (const cell of streakCells(build("L2"))) {
      expect(cell).toContain("lucide-arrow-down")
      expect(cell).toContain("text-red-400")
      expect(cell).not.toContain("lucide-arrow-up")
      expect(cell).toContain("L2")
    }
  })

  it.each([undefined, "", "N/A", "-", "T1"])("renders %j as -- with no arrow", (streak) => {
    const cells = streakCells(build(streak))
    expect(cells).toHaveLength(2)
    for (const cell of cells) {
      expect(cell).toContain("--")
      expect(cell).not.toContain("lucide-arrow")
      expect(cell).not.toContain("N/A")
    }
  })
})
