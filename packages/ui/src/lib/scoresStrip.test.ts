import { describe, it, expect } from "vitest"

import { toStripGames } from "./scoresStrip"
import type { MatchupData } from "../types/sports-types"

function game(gameId: string, overrides: Record<string, unknown> = {}): MatchupData {
  return {
    gameId,
    status: "7:30 PM ET",
    time: "7:30 PM ET",
    gameStatus: 1,
    period: 0,
    gameClock: null,
    away: { abbreviation: "AAA", score: 0 },
    home: { abbreviation: "HHH", score: 0 },
    ...overrides,
  } as unknown as MatchupData
}

describe("toStripGames", () => {
  it("puts live games first, then upcoming, then final, keeping slate order within each", () => {
    const games = toStripGames("nhl", [
      game("final", { gameStatus: 3, status: "Final" }),
      game("soon-1"),
      game("live", { gameStatus: 2, status: "2nd 00:56" }),
      game("soon-2"),
    ])

    expect(games.map((g) => g.key)).toEqual(["nhl-live", "nhl-soon-1", "nhl-soon-2", "nhl-final"])
    expect(games.map((g) => g.label)).toEqual(["2nd 00:56", "7:30 PM ET", "7:30 PM ET", "Final"])
  })

  it("builds the live label from the NBA feed's ISO clock", () => {
    const live = { gameStatus: 2, status: "In Progress" }
    const [q4, ot, ot2] = toStripGames("nba", [
      game("a", { ...live, period: 4, gameClock: "PT03M06.00S" }),
      game("b", { ...live, period: 5, gameClock: "PT00M41.50S" }),
      game("c", { ...live, period: 6, gameClock: "PT05M00.00S" }),
    ])

    expect([q4!.label, ot!.label, ot2!.label]).toEqual(["Q4 3:06", "OT 0:41", "OT2 5:00"])
  })

  it("links only the sports that have a game detail page", () => {
    expect(toStripGames("nfl", [game("401")])[0]!.href).toBe("/game/401?sport=nfl")
    const ranked = game("401", { home: { abbreviation: "HHH", score: 0, rank: 4 } })
    expect(toStripGames("ncaab", [ranked])[0]!.href).toBeNull()
  })

  it("keeps only college games with a ranked team", () => {
    const slate = [
      game("unranked", { away: { abbreviation: "AAA", score: 0, rank: null }, home: { abbreviation: "HHH", score: 0, rank: null } }),
      game("ranked", { away: { abbreviation: "AAA", score: 0, rank: 12 }, home: { abbreviation: "HHH", score: 0, rank: null } }),
    ]

    const games = toStripGames("ncaaf", slate)
    expect(games.map((g) => g.key)).toEqual(["ncaaf-ranked"])
    expect(games[0]!.away.rank).toBe(12)
    // Pro slates have no rankings and are never filtered.
    expect(toStripGames("nba", slate)).toHaveLength(2)
  })
})
