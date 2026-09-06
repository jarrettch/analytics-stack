import { describe, expect, it } from "vitest"

import { getUsageWindow } from "./usageWindow"

describe("getUsageWindow", () => {
  it("uses an exact 30-day window instead of a calendar month", () => {
    const window = getUsageWindow({
      resetsAt: "2026-09-05T00:00:00Z",
      fallbackAnchor: "2026-04-08T00:00:00Z",
      windowDays: 30,
      now: new Date("2026-08-07T00:00:00Z"),
    })

    expect(window.start.toISOString()).toBe("2026-08-06T00:00:00.000Z")
    expect(window.reset.toISOString()).toBe("2026-09-05T00:00:00.000Z")
    expect(window.elapsedDays).toBe(1)
    expect(window.remainingDays).toBe(29)
    expect(window.totalDays).toBe(30)
  })

  it("advances a fallback anchor in 30-day increments", () => {
    const window = getUsageWindow({
      resetsAt: null,
      fallbackAnchor: "2026-04-08T00:00:00Z",
      windowDays: 30,
      now: new Date("2026-11-05T00:00:00Z"),
    })

    expect(window.start.toISOString()).toBe("2026-11-04T00:00:00.000Z")
    expect(window.reset.toISOString()).toBe("2026-12-04T00:00:00.000Z")
    expect(window.totalDays).toBe(30)
  })
})
