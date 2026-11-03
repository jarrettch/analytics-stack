import { describe, expect, it } from "vitest"
import type { MatchupData } from "../types/sports-types"
import {
  type CollegeSport,
  collegeFilterForSlate,
  conferenceOptions,
  createCollegeFilters,
  isCollege,
  passesCollegeFilters,
  updateCollegeFilters,
} from "./collegeFilters"

function college(sport: CollegeSport, rank: number | null, conferenceId: string): Extract<MatchupData, { sport: CollegeSport }> {
  const team = {
    name: "Team", abbreviation: "TEAM", record: "0-0", winPct: 0.5,
    conference: `${sport} conference ${conferenceId}`, conferenceId,
    streak: "N/A", predictedScore: null, winProbability: null,
    score: 0, pointsFor: null, pointsAgainst: null, rank,
    yardsPG: null, yardsAllowedPG: null, turnoverDiff: null,
  }
  const base = {
    gameId: `${sport}-${conferenceId}-${rank}`, status: "7:00 PM ET", date: "2026-11-02", time: "7:00 PM ET",
    venue: "Arena", broadcast: "TV", spreadLine: "N/A", model: "MARKET (DK)",
    gameStatus: 1, period: 0, gameClock: null, seasonType: "regular-season", statsSeason: 2027,
    away: { ...team, rank: null }, home: team,
  }
  return sport === "ncaaf"
    ? { ...base, sport, week: 10, weekLabel: "WK10" }
    : { ...base, sport }
}

describe("dashboard college filters", () => {
  it("defaults NCAAB to ranked games and NCAAF to all games", () => {
    const filters = createCollegeFilters()
    for (const sport of ["ncaaf", "ncaab"] as const) {
      const ranked = college(sport, 5, "2")
      const unranked = college(sport, null, "2")
      expect(passesCollegeFilters(ranked, filters[sport])).toBe(true)
      expect(passesCollegeFilters(unranked, filters[sport])).toBe(sport === "ncaaf")
    }
  })

  it("retains separate conference and ranked preferences when switching sports", () => {
    let filters = createCollegeFilters()
    filters = updateCollegeFilters(filters, "ncaaf", { conferenceId: "8", rankedOnly: true })
    filters = updateCollegeFilters(filters, "ncaab", { conferenceId: "23", rankedOnly: false })
    const football = [college("ncaaf", 5, "8"), college("ncaaf", null, "8"), college("ncaaf", 2, "23")]
    const basketball = [college("ncaab", null, "23"), college("ncaab", 1, "8")]
    expect(football.filter((m) => passesCollegeFilters(m, filters.ncaaf))).toEqual([football[0]])
    expect(basketball.filter((m) => passesCollegeFilters(m, filters.ncaab))).toEqual([basketball[0]])
    filters = updateCollegeFilters(filters, "ncaab", { rankedOnly: true })
    expect(basketball.filter((m) => passesCollegeFilters(m, filters.ncaab))).toEqual([])
    expect(filters.ncaaf).toEqual({ conferenceId: "8", rankedOnly: true })
    expect(filters.ncaab.conferenceId).toBe("23")
  })

  it("keeps conference namespaces separate even when ids overlap", () => {
    const mixed = [college("ncaaf", 1, "2"), college("ncaab", 2, "2")]
    expect(conferenceOptions(mixed, "ncaaf")).toEqual([["2", "ncaaf conference 2"]])
    expect(conferenceOptions(mixed, "ncaab")).toEqual([["2", "ncaab conference 2"]])
  })

  it("temporarily ignores a saved conference absent from the current slate", () => {
    const filters = updateCollegeFilters(createCollegeFilters(), "ncaab", { conferenceId: "23" })
    const current = [college("ncaab", 5, "2")]
    const active = collegeFilterForSlate(filters, "ncaab", conferenceOptions(current, "ncaab"))
    expect(current.filter((m) => passesCollegeFilters(m, active))).toEqual(current)
    expect(active.rankedOnly).toBe(true)
    expect(filters.ncaab.conferenceId).toBe("23")
    expect(collegeFilterForSlate(filters, "ncaab", [["23", "SEC"]]).conferenceId).toBe("23")
  })

  it("accepts a rank or conference on either team", () => {
    const game = college("ncaab", null, "2")
    game.away.rank = 5
    game.away.conferenceId = "23"
    expect(passesCollegeFilters(game, { conferenceId: "23", rankedOnly: true })).toBe(true)
  })

  it("does not filter non-college games", () => {
    const football = college("ncaaf", null, "8")
    if (football.sport !== "ncaaf") throw new Error("Expected football fixture")
    const game: MatchupData = { ...football, sport: "nfl" }
    expect(isCollege(game)).toBe(false)
    expect(passesCollegeFilters(game, { conferenceId: "23", rankedOnly: true })).toBe(true)
    expect(conferenceOptions([game], "ncaab")).toEqual([])
  })
})
