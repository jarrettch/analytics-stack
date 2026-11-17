import { describe, it, expect } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

import { NBABoxScoreTable, PlaysTable } from "./GameDetailView"
import type { NBAPlayerBoxScore, PlayByPlayEvent } from "./types/game-detail-types"

function player(name: string, overrides: Partial<NBAPlayerBoxScore> = {}): NBAPlayerBoxScore {
  return {
    name,
    position: "F",
    minutes: "0",
    points: 0,
    rebounds: 0,
    assists: 0,
    steals: 0,
    blocks: 0,
    turnovers: 0,
    fouls: 0,
    fgm: 0,
    fga: 0,
    threePm: 0,
    threePa: 0,
    ftm: 0,
    fta: 0,
    plusMinus: 0,
    ...overrides,
  }
}

const totals = {
  points: 0, rebounds: 0, assists: 0, steals: 0, blocks: 0, turnovers: 0,
  fgPct: null, threePct: null, ftPct: null,
}

describe("NBABoxScoreTable", () => {
  it("spans a DNP reason across the stat columns", () => {
    const html = renderToStaticMarkup(
      <NBABoxScoreTable
        team="DAL"
        box={{ players: [player("Dereck Lively II", { dnp: "Right Foot, Surgery" })], totals }}
      />,
    )

    expect(html).toContain('colSpan="11"')
    expect(html).toContain("DNP - Right Foot, Surgery")
  })

  it("renders a player who has not checked in as zeros", () => {
    const html = renderToStaticMarkup(
      <NBABoxScoreTable team="DAL" box={{ players: [player("Bench", { dnp: null })], totals }} />,
    )

    expect(html).not.toContain("DNP")
    expect(html).toContain("0-0")
  })
})

describe("PlaysTable", () => {
  const play = (id: string, overrides: Partial<PlayByPlayEvent> = {}): PlayByPlayEvent => ({
    id, clock: "5:00", quarter: 1, team: "MIN", event: `play ${id}`, awayScore: 0, homeScore: 0, ...overrides,
  })

  it("highlights only the plays the feed marks as scoring", () => {
    const html = renderToStaticMarkup(
      <PlaysTable
        plays={[
          play("1"),
          play("2", { scoringPlay: true, scoringType: "TD", awayScore: 7 }),
          // Score corrections move the score without being a scoring play.
          play("3", { scoringPlay: false, awayScore: 6 }),
        ]}
      />,
    )

    expect(html.match(/data-scoring-play/g)).toHaveLength(1)
    expect(html).toContain(">TD</span>")
  })
})
