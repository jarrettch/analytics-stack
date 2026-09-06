import type { GameDetailStatus } from "../types/game-detail-types"

export const GAME_DETAIL_POLL_INTERVAL_MS = 15_000

type PollableGameDetail = {
  status: GameDetailStatus
}

type GameDetailPollingOptions<T extends PollableGameDetail> = {
  initialStatus: GameDetailStatus
  fetchDetail: (signal: AbortSignal) => Promise<T>
  onDetail: (detail: T) => void
  onError?: (error: unknown) => void
  intervalMs?: number
}

export function startGameDetailPolling<T extends PollableGameDetail>({
  initialStatus,
  fetchDetail,
  onDetail,
  onError,
  intervalMs = GAME_DETAIL_POLL_INTERVAL_MS,
}: GameDetailPollingOptions<T>): () => void {
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller: AbortController | undefined

  const schedule = () => {
    if (!stopped) timer = setTimeout(refresh, intervalMs)
  }

  const refresh = async () => {
    controller = new AbortController()
    try {
      const detail = await fetchDetail(controller.signal)
      if (stopped) return
      onDetail(detail)
      if (detail.status !== "final") schedule()
    } catch (error) {
      if (!stopped) {
        onError?.(error)
        schedule()
      }
    } finally {
      controller = undefined
    }
  }

  if (initialStatus !== "final") schedule()

  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    controller?.abort()
  }
}
