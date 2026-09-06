const DAY_MS = 86_400_000
const MIN_ELAPSED_MS = 60 * 60 * 1000

type UsageWindowOptions = {
  resetsAt: string | null
  fallbackAnchor: string
  windowDays: number
  now: Date
}

function timestamp(value: string | null): number | null {
  if (!value) return null
  const parsed = new Date(value).getTime()
  return Number.isFinite(parsed) ? parsed : null
}

export function getUsageWindow({
  resetsAt,
  fallbackAnchor,
  windowDays,
  now,
}: UsageWindowOptions) {
  const days = Number.isFinite(windowDays) && windowDays > 0 ? windowDays : 30
  const windowMs = days * DAY_MS
  const nowMs = now.getTime()
  let resetMs = timestamp(resetsAt) ?? timestamp(fallbackAnchor) ?? nowMs + windowMs

  if (resetMs <= nowMs) {
    resetMs += (Math.floor((nowMs - resetMs) / windowMs) + 1) * windowMs
  } else if (resetMs - nowMs > windowMs) {
    resetMs -= Math.floor((resetMs - nowMs - 1) / windowMs) * windowMs
  }

  const startMs = resetMs - windowMs
  const elapsedMs = Math.max(nowMs - startMs, MIN_ELAPSED_MS)
  return {
    start: new Date(startMs),
    reset: new Date(resetMs),
    elapsedDays: elapsedMs / DAY_MS,
    remainingDays: Math.max((resetMs - nowMs) / DAY_MS, 0),
    totalDays: days,
  }
}
