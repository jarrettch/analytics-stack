import { describe, expect, it } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { MatchupCard } from "./MatchupCard"
import type { NCAABMatchupData } from "./types/sports-types"

function ncaab(): NCAABMatchupData {
  const team = {
    record: "0-0", winPct: 0.5, conference: "ACC", conferenceId: "2",
    streak: "N/A", winProbability: null, pointsFor: null, pointsAgainst: null,
  }
  return {
    sport: "ncaab", gameId: "401909562", status: "7:00 PM ET",
    date: "2026-11-02", slateDate: "2026-11-02", time: "7:00 PM ET",
    venue: "Cameron Indoor Stadium", broadcast: "TV", spreadLine: "DUKE -7.5",
    model: "MARKET (DK)", seasonType: "regular-season", statsSeason: 2027,
    gameStatus: 1, period: 0, gameClock: null, projectedTotal: 145.5,
    away: { ...team, name: "Army", abbreviation: "ARMY", espnSlug: "349", rank: null, predictedScore: 69, score: 41 },
    home: { ...team, name: "Duke", abbreviation: "DUKE", espnSlug: "150", rank: 2, predictedScore: 76.5, score: 58 },
  }
}

function render(matchup: NCAABMatchupData) {
  const html = renderToStaticMarkup(<MatchupCard matchup={matchup} />)
  const scores = [...html.matchAll(/<span class="(font-mono text-2xl font-bold tabular-nums [^"]*)">([^<]*)<\/span>/g)]
    .map((match) => ({ className: match[1], text: match[2] }))
  expect(scores).toHaveLength(2)
  return { html, scores }
}

describe("NCAAB matchup card", () => {
  it.each([[null, null], [null, 76.5], [69, null]])(
    "renders projections %j / %j without highlighting an unknown leader",
    (away, home) => {
      const matchup = ncaab()
      matchup.away.predictedScore = away
      matchup.home.predictedScore = home
      matchup.projectedTotal = null
      const { html, scores } = render(matchup)
      expect(html).toContain("Projected Score")
      expect(scores.map((score) => score.text)).toEqual([away == null ? "--" : String(away), home == null ? "--" : String(home)])
      for (const score of scores) expect(score.className).not.toContain("text-emerald-400")
    },
  )

  it("preserves half-point projections and highlights only the higher score", () => {
    const { scores } = render(ncaab())
    expect(scores.map((score) => score.text)).toEqual(["69", "76.5"])
    expect(scores[0]?.className).not.toContain("text-emerald-400")
    expect(scores[1]?.className).toContain("text-emerald-400")
  })

  it("displays at most one decimal without adding a trailing zero", () => {
    const matchup = ncaab()
    matchup.away.predictedScore = 68.75
    const { scores } = render(matchup)
    expect(scores.map((score) => score.text)).toEqual(["68.8", "76.5"])
  })

  it.each(["2H 5:12", "Halftime", "OT 3:21", "2OT 0:42"])(
    "shows actual live scores with the server status %s",
    (status) => {
      const matchup = ncaab()
      matchup.gameStatus = 2
      matchup.period = 2
      matchup.gameClock = "5:12"
      matchup.status = status
      const { html, scores } = render(matchup)
      expect(scores.map((score) => score.text)).toEqual(["41", "58"])
      expect(html).toContain(status)
      expect(html).not.toContain("Q2")
      expect(html).not.toContain("Projected Score")
      expect(html).not.toContain("76.5")
      expect(html).not.toContain("/game/")
    },
  )

  it("shows actual final scores", () => {
    const matchup = ncaab()
    matchup.gameStatus = 3
    matchup.period = 3
    matchup.status = "F/OT"
    const { html, scores } = render(matchup)
    expect(html).toContain(">Final</span>")
    expect(scores.map((score) => score.text)).toEqual(["41", "58"])
    expect(html).not.toContain("Projected Score")
  })

  it("uses NCAA logos and the poll rank, with basketball-specific stats", () => {
    const matchup = ncaab()
    matchup.home.seed = 12 // Poll rank wins over any seed.
    matchup.home.pointsFor = 82.3
    matchup.home.pointsAgainst = 70
    matchup.home.streak = "W2"
    const { html } = render(matchup)
    expect(html).toContain("teamlogos/ncaa/500/150.png")
    expect(html).not.toContain("teamlogos/ncaab/")
    expect(html).toMatch(/>2<\/span><span[^>]*>Duke<\/span>/)
    expect(html).toContain(">PPG</span><span")
    expect(html).toContain(">Opp PPG</span><span")
    expect(html).toContain(">82.3</span>")
    expect(html).toContain(">70.0</span>")
    expect(html).toMatch(/>PPG<\/span><span[^>]*>--<\/span>/)
    expect(html).toMatch(/>Opp PPG<\/span><span[^>]*>--<\/span>/)
    expect(html).toContain(">ACC</span>")
    expect(html).toContain("W2")
    expect(html).not.toContain("TO+/-")
    expect(html).not.toContain("Off Rating")
  })
})
