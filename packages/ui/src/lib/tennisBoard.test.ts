import { describe, it, expect } from "vitest"

import {
  devigAmerican, extractTennisBoard, groupTennisResults, parseAmericanOdds, tennisGroup, tennisManualMatches,
  tennisProbability, type TennisManualLine, type TennisResult,
} from "./tennisBoard"

function projection(id: string, overrides: Record<string, unknown> = {}, league = "5", playerId = "p1") {
  return {
    type: "projection",
    id,
    attributes: {
      description: "Frances Tiafoe", stat_type: "Fantasy Score", odds_type: "standard", line_score: 23.5,
      start_time: "2026-11-16T08:00:00.000-05:00", status: "pre_game", is_live: false, in_game: false,
      ...overrides,
    },
    relationships: {
      league: { data: { id: league } },
      new_player: { data: { id: playerId } },
      game: { data: { id: "204436" } },
    },
  }
}

const included = [
  { type: "new_player", id: "p1", attributes: { display_name: "Carlos Alcaraz" } },
  { type: "new_player", id: "p2", attributes: { display_name: "Bolelli S / Vavassori A" } },
  { type: "league", id: "5", attributes: { name: "TENNIS" } },
]

const board = (data: unknown[]) => JSON.stringify({ data, included })

describe("extractTennisBoard", () => {
  it("keeps only the fields the models read", () => {
    const parsed = extractTennisBoard(board([projection("16990791")]))
    expect(parsed).toEqual({
      ok: true,
      board: {
        rows: [{
          player: "Carlos Alcaraz", opponent: "Frances Tiafoe", stat: "Fantasy Score", odds_type: "standard",
          line: 23.5, start_time: "2026-11-16T08:00:00.000-05:00", status: "pre_game",
          projection_id: "16990791", game_id: "204436",
        }],
        counts: { "Fantasy Score": 1, "Total Games": 0, "Total Games Won": 0 },
        doublesSkipped: 0,
        notPreGameSkipped: 0,
      },
    })
  })

  it("keeps pre-match rows that are also offered live, and Aces as model input", () => {
    const parsed = extractTennisBoard(board([
      projection("1", { stat_type: "Total Games Won", is_live: true, in_game: true }),
      projection("2", { stat_type: "Aces" }),
      projection("3", { stat_type: "Break Points Won" }),
    ]))
    expect(parsed.ok && parsed.board.rows.map((r) => r.stat)).toEqual(["Total Games Won", "Aces"])
    expect(parsed.ok && parsed.board.counts["Total Games Won"]).toBe(1)
  })

  it("drops doubles and rows that are not pre-game, and counts them", () => {
    const parsed = extractTennisBoard(board([
      projection("1"),
      projection("2", {}, "5", "p2"),
      projection("3", { status: "in_progress" }),
    ]))
    expect(parsed.ok && [parsed.board.rows.length, parsed.board.doublesSkipped, parsed.board.notPreGameSkipped]).toEqual([1, 1, 1])
  })

  it("refuses a board with no tennis rows, even though it has Fantasy Score", () => {
    const parsed = extractTennisBoard(board([projection("1", {}, "7")]))
    expect(parsed).toEqual({ ok: false, error: expect.stringContaining("No tennis rows") })
  })

  it("refuses text that is not a projections payload", () => {
    expect(extractTennisBoard("<html>").ok).toBe(false)
    expect(extractTennisBoard('{"data": 1}').ok).toBe(false)
    expect(extractTennisBoard("null").ok).toBe(false)
  })
})

const NOW = new Date("2026-11-16T10:00:00Z")

function result(overrides: Partial<TennisResult> = {}): TennisResult {
  return {
    stat: "Fantasy Score", player: "Carlos Alcaraz", opponent: "Frances Tiafoe",
    start_time: "2026-11-16T08:00:00-05:00", odds_type: "standard", line: 23.5, projection_id: "1",
    game_id: "1", event_id: "e1", flags: [], lean: "over", unpriced_reason: null, p_over: 0.62, p_under: 0.38,
    ...overrides,
  }
}

describe("tennisGroup", () => {
  it("sorts results into the spec's groups", () => {
    expect(tennisGroup(result(), NOW)).toBe("actionable")
    expect(tennisGroup(result({ flags: ["stale_odds"] }), NOW)).toBe("provisional")
    expect(tennisGroup(result({ flags: ["assumed_aces"] }), NOW)).toBe("provisional")
    expect(tennisGroup(result({ flags: ["fails_bias_check"] }), NOW)).toBe("no_play")
    expect(tennisGroup(result({ lean: null, flags: ["ml_only"] }), NOW)).toBe("no_play")
    expect(tennisGroup(result({ lean: null }), NOW)).toBe("no_play")
    expect(tennisGroup(result({ lean: null, unpriced_reason: "no_book_line" }), NOW)).toBe("unpriced")
  })

  it("never plays an under on a goblin or demon", () => {
    expect(tennisGroup(result({ lean: "under", odds_type: "standard" }), NOW)).toBe("actionable")
    expect(tennisGroup(result({ lean: "under", odds_type: "demon", flags: ["over_only"] }), NOW)).toBe("no_play")
    expect(tennisGroup(result({ lean: "over", odds_type: "goblin" }), NOW)).toBe("actionable")
  })

  it("moves a row to started once its start time passes, whatever else is true of it", () => {
    const later = new Date("2026-11-16T14:00:00Z")
    expect(tennisGroup(result(), later)).toBe("started")
    expect(tennisGroup(result({ unpriced_reason: "no_book_line" }), later)).toBe("started")
    expect(tennisGroup(result({ flags: ["started"] }), NOW)).toBe("started")
  })
})

describe("manual lines", () => {
  const typed: TennisManualLine = {
    game_id: "7", player: "Qual Ifier", tour: "atp", ml_player: 150, ml_opponent: -180,
    total_line: 22.5, total_over: null, total_under: null, aces_player: null, aces_opponent: null,
  }

  it("keeps a typed-in match out of the actionable group", () => {
    expect(tennisGroup(result({ flags: ["manual_odds"] }), NOW)).toBe("provisional")
    expect(tennisGroup(result({ flags: ["manual_odds", "fails_bias_check"] }), NOW)).toBe("no_play")
  })

  it("lists matches missing a book line once each, then the ones already entered", () => {
    const results = [
      result({ game_id: "1" }),
      result({ game_id: "5", player: "Late Starter", unpriced_reason: "no_book_line", lean: null, start_time: "2026-11-16T12:00:00-05:00" }),
      result({ game_id: "4", player: "Chal Lenger", unpriced_reason: "no_book_line", lean: null }),
      result({ game_id: "4", player: "Chal Lenger", stat: "Total Games", unpriced_reason: "no_book_line", lean: null }),
      result({ game_id: "6", player: "Only Moneyline", lean: null, flags: ["ml_only"], tour: "wta", stat: "Total Games" }),
      result({ game_id: "7", player: "Chal Lenger", opponent: "Qual Ifier", flags: ["manual_odds"], tour: "atp" }),
      result({ game_id: "8", player: "Two Named", unpriced_reason: "ambiguous_match", lean: null }),
      result({ game_id: "9", player: "Under Way", unpriced_reason: "no_book_line", lean: null, start_time: "2026-11-16T04:00:00-05:00" }),
    ]
    const matches = tennisManualMatches({ results, manual_lines: [typed] }, NOW)
    expect(matches.map((m) => m.game_id)).toEqual(["4", "6", "5", "7"])
    expect(matches[1]!.tour).toBe("wta")
    // An entered match reads from the side it was typed on.
    expect(matches[3]).toMatchObject({ player: "Qual Ifier", opponent: "Chal Lenger", entered: typed })
  })

  it("reads American prices and nothing else", () => {
    expect([parseAmericanOdds("+150"), parseAmericanOdds(" -180 "), parseAmericanOdds("100")]).toEqual([150, -180, 100])
    expect(["1.9", "-50", "abc", "", "--110", "20000"].map(parseAmericanOdds)).toEqual([null, null, null, null, null, null])
  })

  it("splits the margin out of a two-way price", () => {
    expect(devigAmerican(-110, -110)).toBeCloseTo(0.5)
    expect(devigAmerican(-200, 165)).toBeCloseTo(0.6386, 3)
  })
})

describe("tennisGroup on Games Won", () => {
  const gamesWon = (p: number, overrides: Partial<TennisResult> = {}) =>
    result({ stat: "Total Games Won", p_over: p, p_under: 1 - p, ...overrides })

  it("needs a 55% lean to be a play", () => {
    expect(tennisGroup(gamesWon(0.549), NOW)).toBe("no_play")
    expect(tennisGroup(gamesWon(0.55), NOW)).toBe("actionable")
    expect(tennisGroup(gamesWon(0.4, { lean: "under" }), NOW)).toBe("actionable")
    expect(tennisGroup(gamesWon(0.549, { flags: ["stale_odds"] }), NOW)).toBe("no_play")
    expect(tennisGroup(gamesWon(0.6, { flags: ["stale_odds"] }), NOW)).toBe("provisional")
  })

  it("leaves the other stats at any lean", () => {
    expect(tennisGroup(result({ p_over: 0.507, p_under: 0.493 }), NOW)).toBe("actionable")
    expect(tennisGroup(result({ stat: "Total Games", p_over: 0.507, p_under: 0.493 }), NOW)).toBe("actionable")
  })
})

describe("groupTennisResults", () => {
  it("orders a group by the lean's probability", () => {
    const groups = groupTennisResults([
      result({ player: "A", p_over: 0.55, p_under: 0.45 }),
      result({ player: "B", lean: "under", p_over: 0.3, p_under: 0.7 }),
      result({ player: "C", p_over: 0.61, p_under: 0.39 }),
    ], NOW, false)
    expect(groups.actionable.map((r) => r.player)).toEqual(["B", "C", "A"])
    expect(tennisProbability(groups.actionable[0]!)).toBe(0.7)
  })

  it("orders by odds type first when every type is shown", () => {
    const groups = groupTennisResults([
      result({ player: "goblin", odds_type: "goblin", p_over: 0.9, p_under: 0.1 }),
      result({ player: "standard", p_over: 0.55, p_under: 0.45 }),
    ], NOW, true)
    expect(groups.actionable.map((r) => r.player)).toEqual(["standard", "goblin"])
  })
})
