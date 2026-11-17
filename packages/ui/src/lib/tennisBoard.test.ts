import { describe, it, expect } from "vitest"

import { extractTennisBoard, groupTennisResults, tennisGroup, tennisProbability, type TennisResult } from "./tennisBoard"

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
