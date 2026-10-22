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

// The sparkline viewBox is 300 units wide, so more than ~2 points per unit is
// detail no one can see. Cap well above that and halve resolution when we cross
// it, rather than dropping the oldest points — a FIFO cap would turn the chart
// into a sliding window and lose tip-off.
const MAX_SNAPSHOTS = 600

/**
 * Halves resolution in place once a history exceeds MAX_SNAPSHOTS, always
 * keeping the first and last points so the rendered span still runs
 * tip-off -> now. Visually lossless at the size the sparkline renders.
 */
export function capHistory(history: WinProbSnapshot[]): void {
  if (history.length <= MAX_SNAPSHOTS) return

  const kept: WinProbSnapshot[] = []
  for (let i = 0; i < history.length; i += 2) kept.push(history[i]!)

  // Keep the newest sample even when the stride skipped it — MLB's x-axis
  // continues from last.timeRemaining, so losing the tail would rewind it.
  const newest = history[history.length - 1]!
  if (kept[kept.length - 1] !== newest) kept.push(newest)

  history.length = 0
  for (const snapshot of kept) history.push(snapshot)
}

// Finished games keep their history — their cards still render a sparkline
// (gameStatus >= 2), so evicting on Final would blank the chart. What needs
// bounding is games that roll off the slate entirely at the date change.
const MAX_GAMES = 100

/**
 * Marks a game as most-recently-used and evicts the coldest entries once we
 * hold more than MAX_GAMES. Map iterates in insertion order, so re-inserting on
 * touch makes the front of the map the least-recently-used end.
 *
 * Anything currently on screen is touched every poll, so eviction only ever
 * reaches games the user has moved on from — a previous day's slate, or a sport
 * they switched away from long ago.
 */
function touchGame(key: string): void {
  const history = winProbHistory.get(key)
  if (history !== undefined) {
    winProbHistory.delete(key)
    winProbHistory.set(key, history)
  }

  while (winProbHistory.size > MAX_GAMES) {
    const coldest = winProbHistory.keys().next()
    if (coldest.done) break
    winProbHistory.delete(coldest.value)
  }
}

/** Caps, stores, and marks a freshly hydrated timeline as most-recently-used. */
function storeHistory(key: string, history: WinProbSnapshot[]): WinProbSnapshot[] {
  capHistory(history)
  winProbHistory.set(key, history)
  touchGame(key)
  return history
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
    capHistory(history)
  }
  touchGame(gameId)

  return winProb
}

export function getHistory(gameId: string): WinProbSnapshot[] {
  const history = winProbHistory.get(gameId)
  if (history === undefined) return []
  touchGame(gameId)
  return history
}

// ─── MLB Win Probability (API-sourced) ───────────────────────────────────────

interface MLBWinProbPlay {
  atBatIndex: number
  homeTeamWinProbability: number  // 0–100
  awayTeamWinProbability: number
  homeScore: number
  awayScore: number
  about?: { inning: number; halfInning: "top" | "bottom" }
}


/**
 * Fetches full play-by-play win probability history from the MLB Stats API.
 * Call once on mount for live games — seeds the sparkline from the current point in the game.
 */
export async function hydrateMLBWinProbHistory(
  gamePk: string | number,
): Promise<WinProbSnapshot[]> {
  const key = String(gamePk)

  // If we already have history (from a previous mount), return it instead of re-fetching
  const existing = winProbHistory.get(key)
  if (existing && existing.length > 0) return existing

  try {
    const res = await fetch(`/api/mlb-stats/game/${gamePk}/winProbability`)
    if (!res.ok) return []
    const plays: MLBWinProbPlay[] = await res.json()
    if (!plays.length) return []

    const history: WinProbSnapshot[] = plays.map((play) => ({
      timestamp: Date.now(),
      winProb: play.homeTeamWinProbability / 100,
      scoreDiff: play.homeScore - play.awayScore,
      timeRemaining: play.atBatIndex,  // used as x-axis unit for MLB sparkline
      period: play.about?.inning ?? 0,
    }))

    return storeHistory(key, history)
  } catch {
    return []
  }
}

/**
 * Appends a snapshot from the latest contextMetrics poll.
 * timeRemaining continues from where atBatIndex left off, keyed per gamePk.
 */
export function recordMLBSnapshot(
  gamePk: string | number,
  winProb: number,
  scoreDiff: number,
  inning: number,
): void {
  const key = String(gamePk)
  if (!winProbHistory.has(key)) winProbHistory.set(key, [])
  const history = winProbHistory.get(key)!
  const last = history[history.length - 1]

  if (last && Math.abs(last.winProb - winProb) < 0.001 && last.scoreDiff === scoreDiff) return

  // Continue x-axis from where hydration left off
  const nextIndex = last ? last.timeRemaining + 1 : 0

  history.push({
    timestamp: Date.now(),
    winProb,
    scoreDiff,
    timeRemaining: nextIndex,
    period: inning,
  })
  capHistory(history)
  touchGame(key)
}

// ─── NBA / WNBA Win Probability (ESPN-sourced) ──────────────────────────────

export type ESPNBasketballSport = 'nba' | 'wnba'

const QUARTER_SECONDS: Record<ESPNBasketballSport, number> = { nba: 720, wnba: 600 }

interface ESPNWinProbPlay {
  playId: string
  homeWinPercentage: number
  period: number
  clock: string
  homeScore: number
  awayScore: number
  timestamp: string | null
}

function espnClockSeconds(clock: string): number {
  if (!clock) return 0
  if (!clock.includes(':')) {
    const seconds = Number.parseFloat(clock)
    return Number.isFinite(seconds) ? seconds : 0
  }
  const [minutes = '0', seconds = '0'] = clock.split(':')
  const total = Number.parseInt(minutes, 10) * 60 + Number.parseFloat(seconds)
  return Number.isFinite(total) ? total : 0
}

export function espnTimeRemainingSeconds(sport: ESPNBasketballSport, period: number, clock: string): number {
  const quarter = QUARTER_SECONDS[sport]
  const clockSeconds = espnClockSeconds(clock)
  if (period <= 0) return quarter * 4
  if (period <= 4) return (4 - period) * quarter + clockSeconds
  return clockSeconds
}

/** Fetch ESPN's source-provided NBA or WNBA probability timeline. */
export async function hydrateESPNWinProbHistory(
  sport: ESPNBasketballSport,
  gameId: string,
  apiBaseUrl = '',
): Promise<WinProbSnapshot[]> {
  const key = String(gameId)
  try {
    const res = await fetch(`${apiBaseUrl}/api/${sport}/game/${gameId}/win-probability`)
    if (!res.ok) return []
    const plays: ESPNWinProbPlay[] = await res.json()
    if (!plays.length) return []

    const history = plays.map((play) => {
      const parsedTimestamp = play.timestamp ? new Date(play.timestamp).getTime() : NaN
      return {
        timestamp: Number.isFinite(parsedTimestamp) ? parsedTimestamp : Date.now(),
        winProb: play.homeWinPercentage,
        scoreDiff: play.homeScore - play.awayScore,
        timeRemaining: espnTimeRemainingSeconds(sport, play.period, play.clock),
        period: play.period,
      }
    })

    return storeHistory(key, history)
  } catch {
    return []
  }
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
