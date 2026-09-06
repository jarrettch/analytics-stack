import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { startGameDetailPolling } from "./gameDetailPolling"

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe("startGameDetailPolling", () => {
  it("refreshes an active game on the configured interval", async () => {
    const nextDetail = { status: "live" as const, score: 3 }
    const fetchDetail = vi.fn().mockResolvedValue(nextDetail)
    const onDetail = vi.fn()

    const stop = startGameDetailPolling({
      initialStatus: "live",
      fetchDetail,
      onDetail,
      intervalMs: 15_000,
    })

    expect(fetchDetail).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(15_000)

    expect(fetchDetail).toHaveBeenCalledOnce()
    expect(onDetail).toHaveBeenCalledWith(nextDetail)
    stop()
  })

  it("stops after receiving the final game state", async () => {
    const fetchDetail = vi.fn().mockResolvedValue({ status: "final" as const })

    startGameDetailPolling({
      initialStatus: "live",
      fetchDetail,
      onDetail: vi.fn(),
      intervalMs: 15_000,
    })

    await vi.advanceTimersByTimeAsync(60_000)

    expect(fetchDetail).toHaveBeenCalledOnce()
  })

  it("keeps polling after a failed refresh", async () => {
    const nextDetail = { status: "final" as const, score: 5 }
    const fetchDetail = vi
      .fn()
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(nextDetail)
    const onDetail = vi.fn()
    const onError = vi.fn()

    startGameDetailPolling({
      initialStatus: "live",
      fetchDetail,
      onDetail,
      onError,
      intervalMs: 15_000,
    })

    await vi.advanceTimersByTimeAsync(30_000)

    expect(fetchDetail).toHaveBeenCalledTimes(2)
    expect(onError).toHaveBeenCalledOnce()
    expect(onDetail).toHaveBeenCalledWith(nextDetail)
  })

  it("does not poll a game that is already final", async () => {
    const fetchDetail = vi.fn()

    startGameDetailPolling({
      initialStatus: "final",
      fetchDetail,
      onDetail: vi.fn(),
      intervalMs: 15_000,
    })

    await vi.advanceTimersByTimeAsync(60_000)

    expect(fetchDetail).not.toHaveBeenCalled()
  })
})
