import { describe, it, expect, beforeEach } from "vitest"

import {
  winProbHistory,
  recordSnapshot,
  recordMLBSnapshot,
  getHistory,
  capHistory,
  espnTimeRemainingSeconds,
  type WinProbSnapshot,
} from "./winProb"

const clock = (secondsLeft: number) =>
  `PT${Math.floor(secondsLeft / 60)}M${String(secondsLeft % 60).padStart(2, "0")}S`

/** Mirrors WinProbSparkline's x-axis so span assertions track what's drawn. */
const elapsed = (period: number, timeRemaining: number) =>
  period <= 4 ? 2880 - timeRemaining : 2880 + (period - 4) * 300 - timeRemaining

beforeEach(() => {
  winProbHistory.clear()
})

describe("capHistory", () => {
  it("bounds a history that would otherwise grow all game", () => {
    for (let i = 0; i < 5000; i++) {
      recordSnapshot("G1", 50 + (i % 40), 48, 1 + Math.floor(i / 1250), clock(700 - (i % 700)))
    }
    expect(getHistory("G1").length).toBeLessThanOrEqual(600)
  })

  it("keeps tip-off, so the chart is not a sliding window", () => {
    recordSnapshot("G2", 0, 0, 1, clock(720))
    const tipoff = { ...getHistory("G2")[0]! }

    for (let i = 1; i < 5000; i++) {
      recordSnapshot("G2", i % 90, 48, 1 + Math.floor(i / 1250), clock(700 - (i % 700)))
    }
    expect({ ...getHistory("G2")[0]! }).toEqual(tipoff)
  })

  it("keeps the newest point even when the stride would skip it", () => {
    for (let i = 0; i < 5000; i++) {
      recordSnapshot("G3", 50 + (i % 40), 48, 2, clock(600 - (i % 600)))
    }
    recordSnapshot("G3", 121, 99, 4, clock(1))
    expect(getHistory("G3").at(-1)!.scoreDiff).toBe(22)
  })

  it("preserves the rendered span, dropping only resolution", () => {
    const raw: WinProbSnapshot[] = []
    for (let i = 0; i < 5000; i++) {
      recordSnapshot("G4", 50 + (i % 40), 48, 1 + Math.floor(i / 1250), clock(700 - (i % 700)))
      raw.push(getHistory("G4").at(-1)!)
    }
    const kept = getHistory("G4")
    const spanOf = (h: WinProbSnapshot[]) =>
      elapsed(h.at(-1)!.period, h.at(-1)!.timeRemaining) - elapsed(h[0]!.period, h[0]!.timeRemaining)

    expect(spanOf(kept)).toBe(spanOf(raw))
    // The sparkline viewBox is 300 units wide; stay above that.
    expect(kept.length).toBeGreaterThanOrEqual(300)
  })

  it("leaves the MLB x-axis monotonic across a decimation", () => {
    // recordMLBSnapshot derives x from last.timeRemaining + 1, so dropping the
    // tail would rewind the axis.
    for (let i = 0; i < 3000; i++) {
      recordMLBSnapshot("777", 0.4 + (i % 100) / 1000, i % 5, 1 + (i % 9))
    }
    const h = getHistory("777")
    for (let i = 1; i < h.length; i++) {
      expect(h[i]!.timeRemaining).toBeGreaterThan(h[i - 1]!.timeRemaining)
    }
  })

  it("is a no-op below the cap", () => {
    const h: WinProbSnapshot[] = Array.from({ length: 599 }, (_, i) => ({
      timestamp: i, winProb: 0.5, scoreDiff: 0, timeRemaining: i, period: 1,
    }))
    capHistory(h)
    expect(h).toHaveLength(599)
  })
})

describe("LRU game cap", () => {
  it("evicts the coldest games, never the newest", () => {
    for (let g = 0; g < 150; g++) recordSnapshot(`game-${g}`, 10, 8, 1, clock(700))

    expect(winProbHistory.size).toBeLessThanOrEqual(100)
    expect(winProbHistory.has("game-149")).toBe(true)
    expect(winProbHistory.has("game-0")).toBe(false)
  })

  it("never evicts a game that is still being polled", () => {
    recordSnapshot("onscreen", 0, 0, 1, clock(720))
    for (let g = 0; g < 300; g++) {
      recordSnapshot(`filler-${g}`, 10, 8, 1, clock(700))
      recordSnapshot("onscreen", g % 50, 8, 1, clock(699 - (g % 600)))
    }
    expect(winProbHistory.has("onscreen")).toBe(true)
  })

  it("keeps a finished game alive while its card still reads it", () => {
    // Recording early-returns on final, so only getHistory keeps these hot.
    recordSnapshot("final-game", 101, 99, 4, clock(0))
    for (let g = 0; g < 300; g++) {
      recordSnapshot(`filler-${g}`, 10, 8, 1, clock(700))
      getHistory("final-game") // the card re-renders each poll
    }
    expect(winProbHistory.has("final-game")).toBe(true)
  })
})

describe("espnTimeRemainingSeconds", () => {
  it("uses 12-minute NBA quarters and 10-minute WNBA quarters", () => {
    expect(espnTimeRemainingSeconds("nba", 0, "")).toBe(2880)
    expect(espnTimeRemainingSeconds("wnba", 0, "")).toBe(2400)
    expect(espnTimeRemainingSeconds("nba", 2, "5:00")).toBe(2 * 720 + 300)
    expect(espnTimeRemainingSeconds("wnba", 2, "5:00")).toBe(2 * 600 + 300)
  })

  it("reads ESPN's sub-minute clock", () => {
    expect(espnTimeRemainingSeconds("nba", 4, "45.3")).toBeCloseTo(45.3)
  })
})
