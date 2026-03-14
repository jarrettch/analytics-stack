// ─── Game Clock ─────────────────────────────────────────────────────────────

function parseGameClockSeconds(gameClock: string): number {
  // Handles "PT12M00S", "PT12M" (exact minute), "PT45.5S" (sub-minute)
  const match = gameClock.match(/PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?/)
  if (!match) return 0
  const minutes = parseInt(match[1] || '0', 10)
  const seconds = parseFloat(match[2] || '0')
  const total = minutes * 60 + seconds
  return isNaN(total) ? 0 : total
}

export function totalTimeRemainingSeconds(period: number, gameClock: string): number {
  const clockSeconds = parseGameClockSeconds(gameClock)
  if (period <= 0) return 2880
  if (period <= 4) return (4 - period) * 720 + clockSeconds
  // OT: each period is 5 min; if clock is 0, game is over
  if (clockSeconds === 0) return 0
  return clockSeconds
}

// ─── Win Probability Model ───────────────────────────────────────────────────

function logistic(x: number): number {
  return 1 / (1 + Math.exp(-x))
}

// k calibrated so: up 3 with 10s left ≈ 96%, up 16 with 36 min left ≈ 77%
function getK(pace = 100): number {
  if (pace > 102) return 3.0  // high pace: leads less safe
  if (pace < 98) return 4.0   // low pace: leads safer
  return 3.5
}

export function naiveWinProb(
  homeScore: number,
  awayScore: number,
  period: number,
  gameClock: string,
  pregameSpread = 0, // home perspective, negative = home favored
  pace = 100,
): number {
  const scoreDiff = homeScore - awayScore
  const timeRemaining = totalTimeRemainingSeconds(period, gameClock)

  if (timeRemaining === 0) {
    return scoreDiff > 0 ? 1 : scoreDiff < 0 ? 0 : 0.5
  }

  const totalSeconds = 2880
  // OT: clamp spread fade to 0 — clock reset would cause a jarring sparkline spike
  const spreadFade = period > 4 ? 0 : timeRemaining / totalSeconds
  const k = getK(pace)

  const adjustedDiff = scoreDiff + (-pregameSpread * spreadFade)
  const denom = Math.sqrt(timeRemaining + 1)

  return logistic((adjustedDiff * k) / denom)
}

// ─── Snapshot Accumulation ───────────────────────────────────────────────────

export interface WinProbSnapshot {
  timestamp: number
  winProb: number       // 0–1, home perspective, raw (unclamped)
  scoreDiff: number     // homeScore - awayScore
  timeRemaining: number // seconds
  period: number
}

// Module-level singleton — shared across all components for the browser session
export const winProbHistory = new Map<string, WinProbSnapshot[]>()

/**
 * Hydrates the full win prob history from the NBA CDN play-by-play feed.
 * Call once on component mount — rebuilds the timeline from tip-off to now.
 */
export async function hydrateWinProbHistory(
  gameId: string,
  pregameSpread: number,
  pace = 100,
): Promise<WinProbSnapshot[]> {
  const url = `/api/nba-cdn/playbyplay/playbyplay_${gameId}.json`
  try {
    const res = await fetch(url)
    if (!res.ok) return []
    const data = await res.json()
    const actions: Record<string, unknown>[] = data.game?.actions ?? []
    const history: WinProbSnapshot[] = []

    // Start at -1 so the 0-0 tip-off triggers the first snapshot
    let lastHomeScore = -1
    let lastAwayScore = -1

    for (const a of actions) {
      const homeScore = parseInt((a.scoreHome as string) || '0', 10)
      const awayScore = parseInt((a.scoreAway as string) || '0', 10)
      const isPeriodMarker = a.actionType === 'period'

      if (homeScore !== lastHomeScore || awayScore !== lastAwayScore || isPeriodMarker) {
        const period = a.period as number
        const clock = (a.clock as string) ?? ''
        const winProb = naiveWinProb(homeScore, awayScore, period, clock, pregameSpread, pace)
        const timeRemaining = totalTimeRemainingSeconds(period, clock)

        history.push({
          timestamp: a.timeActual ? new Date(a.timeActual as string).getTime() : Date.now(),
          winProb,
          scoreDiff: homeScore - awayScore,
          timeRemaining,
          period,
        })

        lastHomeScore = homeScore
        lastAwayScore = awayScore
      }
    }

    winProbHistory.set(gameId, history)
    return history
  } catch {
    return []
  }
}

/**
 * Appends a snapshot if the game state changed — call on each poll update.
 */
export function recordSnapshot(
  gameId: string,
  homeScore: number,
  awayScore: number,
  period: number,
  gameClock: string,
  pregameSpread = 0,
  pace = 100,
): number {
  const winProb = naiveWinProb(homeScore, awayScore, period, gameClock, pregameSpread, pace)
  const timeRemaining = totalTimeRemainingSeconds(period, gameClock)
  const scoreDiff = homeScore - awayScore

  if (!winProbHistory.has(gameId)) winProbHistory.set(gameId, [])
  const history = winProbHistory.get(gameId)!
  const last = history[history.length - 1]

  if (!last || last.scoreDiff !== scoreDiff || last.timeRemaining !== timeRemaining) {
    history.push({ timestamp: Date.now(), winProb, scoreDiff, timeRemaining, period })
  }

  return winProb
}

export function getHistory(gameId: string): WinProbSnapshot[] {
  return winProbHistory.get(gameId) ?? []
}

// ─── CLV / ML Edge Calculator ─────────────────────────────────────────────────

/**
 * home/away/over/under: spread or total bet — shows CLV points, hides edge
 * home_ml/away_ml:      moneyline bet — shows edge %, hides CLV points
 */
export type BetSide = 'home' | 'away' | 'over' | 'under' | 'home_ml' | 'away_ml'

function impliedProb(odds: number): number {
  return odds < 0 ? -odds / (-odds + 100) : 100 / (odds + 100)
}

function deVig(oddsHome: number, oddsAway: number): { home: number; away: number } {
  const raw = impliedProb(oddsHome) + impliedProb(oddsAway)
  return {
    home: impliedProb(oddsHome) / raw,
    away: impliedProb(oddsAway) / raw,
  }
}

function decimalProfit(odds: number): number {
  return odds < 0 ? 100 / -odds : odds / 100
}

export interface CLVResult {
  clvPoints: number        // positive = beat the close (spread/total only; 0 for ML)
  edgeVsClose: number      // decimal edge vs closing no-vig probability
  breakEvenProb: number
  closingNoVigProb: number
  isML: boolean
}

/**
 * All spread/total lines must be in HOME perspective before calling (apply adapter).
 * ML bets: pass yourLine=0, closeLine=0 — only odds are used.
 *
 * Adapter (apply in UI before calling):
 *   spread/away: normalizedLine = -userTypedLine  (ticket +5.5 → -5.5)
 *   totals:      no normalization needed
 *   ML:          no line needed
 */
export function calcCLV(
  yourSide: BetSide,
  yourLine: number,       // home-perspective spread, raw total, or 0 for ML
  yourOdds: number,
  closeLine: number,      // pregameSpread (spread) | overUnder (total) | 0 (ML)
  closeOddsHome: number,  // spread: home odds  |  total: over odds  |  ML: home ML
  closeOddsAway: number,  // spread: away odds  |  total: under odds |  ML: away ML
): CLVResult {
  const noVig = deVig(closeOddsHome, closeOddsAway)
  const closingNoVigProb =
    yourSide === 'home' || yourSide === 'over' || yourSide === 'home_ml'
      ? noVig.home
      : noVig.away

  const breakEvenProb = impliedProb(yourOdds)
  const b = decimalProfit(yourOdds)
  const edgeVsClose = closingNoVigProb * (b + 1) - 1

  const isML = yourSide === 'home_ml' || yourSide === 'away_ml'

  // CLV points: positive = beat the close
  // home/under: yourLine - closeLine  |  away/over: closeLine - yourLine
  const clvPoints = isML
    ? 0
    : yourSide === 'home' || yourSide === 'under'
      ? yourLine - closeLine
      : closeLine - yourLine

  return { clvPoints, edgeVsClose, breakEvenProb, closingNoVigProb, isML }
}
