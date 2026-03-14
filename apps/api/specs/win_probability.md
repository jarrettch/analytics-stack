# NBA Live Win Probability — Full Implementation Spec

## Data Flow Overview
```
GitHub Actions cron (pre-game)
  → fetches Odds API
  → upserts to Supabase nba_odds table (keyed by game_date + matchup_key)
  → { homeSpread, awaySpread, spreadOddsHome, spreadOddsAway, overUnder, overOdds, underOdds, homeMoneyline, awayMoneyline }

API /api/nba/today (every 20-60s, cached)
  → loads persisted odds from Supabase
  → for live/final games (gameStatus >= 2): uses persisted (closing) odds — frozen at game start
  → for scheduled games: merges with fresh Odds API lines
  → assembles scoreboard + standings + advanced stats + odds into MatchupData[]

Frontend (LiveWinProbSection)
  → on mount: hydrateWinProbHistory() — fetches NBA CDN PBP feed, reconstructs full timeline
  → on poll update: recordSnapshot() — appends snapshot when score/clock changes
  → renders sparkline + current win prob bar
```

## 1. Parse Game Clock → Time Remaining (seconds)
```ts
function parseGameClock(gameClock: string): number {
  // Handles "PT12M00S", "PT12M" (exact minute), "PT45.5S" (sub-minute)
  const match = gameClock.match(/PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?/);
  if (!match) return 0;
  const minutes = parseInt(match[1] || '0', 10);
  const seconds = parseFloat(match[2] || '0');
  const total = minutes * 60 + seconds;
  return isNaN(total) ? 0 : total;
}

function totalTimeRemainingSeconds(period: number, gameClock: string): number {
  const clockSeconds = parseGameClock(gameClock);
  if (period <= 0) return 2880;
  if (period <= 4) return (4 - period) * 720 + clockSeconds;
  // OT: each OT period is 5 min
  // Guard: if OT ended (period > 4 and clock is 0), snap to game-over
  if (clockSeconds === 0) return 0;
  return clockSeconds;
}
```

## 2. Win Probability Model
```ts
function logistic(x: number): number {
  return 1 / (1 + Math.exp(-x));
}

// Dynamic k based on game pace:
// High pace (>102): leads are less safe, use lower k (3.0)
// Low pace (<98): leads are safer, use higher k (4.0)
function getK(pace: number = 100): number {
  if (pace > 102) return 3.0;
  if (pace < 98) return 4.0;
  return 3.5;
}

function naiveWinProb(
  homeScore: number,
  awayScore: number,
  period: number,
  gameClock: string,
  pregameSpread: number = 0, // home perspective, e.g. -5.5 means home favored
  pace: number = 100         // average of both teams' pace
): number {
  const scoreDiff = homeScore - awayScore;
  const timeRemaining = totalTimeRemainingSeconds(period, gameClock);

  if (timeRemaining === 0) {
    return scoreDiff > 0 ? 1 : scoreDiff < 0 ? 0 : 0.5;
  }

  const totalSeconds = 2880; // 48 min regulation
  // OT: clamp spread fade to 0 — by then the score is tied and the pregame
  // line has no predictive value. Without this, the clock reset back to 300s
  // would cause spreadFade to jump from 0 → ~0.104, spiking the sparkline.
  const spreadFade = period > 4 ? 0 : timeRemaining / totalSeconds;
  const k = getK(pace);

  // pregameSpread is negative when home is favored (e.g. -5.5)
  // flip sign: home favored → positive adjustment, fades to 0 as game ends
  const adjustedDiff = scoreDiff + (-pregameSpread * spreadFade);
  const denom = Math.sqrt(timeRemaining + 1);

  return logistic((adjustedDiff * k) / denom);
}
```

## 3. Snapshot Accumulation (in-memory)
```ts
interface WinProbSnapshot {
  timestamp: number;       // Date.now()
  winProb: number;         // 0-1, home perspective (raw, unclamped)
  scoreDiff: number;       // homeScore - awayScore
  timeRemaining: number;   // seconds
  period: number;
}

// one map per session, keyed by gameId
const winProbHistory = new Map<string, WinProbSnapshot[]>();

// Hydrate full timeline from NBA CDN PBP feed — call once on mount
async function hydrateWinProbHistory(
  gameId: string,
  pregameSpread: number,
  pace: number = 100
): Promise<WinProbSnapshot[]> {
  const url = `/api/nba-cdn/playbyplay/playbyplay_${gameId}.json`; // proxied via Next.js rewrites
  const res = await fetch(url);
  if (!res.ok) return [];
  const data = await res.json();
  const actions = data.game?.actions ?? [];
  const history: WinProbSnapshot[] = [];

  let lastHomeScore = -1;
  let lastAwayScore = -1;

  for (const a of actions) {
    const homeScore = parseInt(a.scoreHome || '0', 10);
    const awayScore = parseInt(a.scoreAway || '0', 10);
    const isPeriodMarker = a.actionType === 'period';

    if (homeScore !== lastHomeScore || awayScore !== lastAwayScore || isPeriodMarker) {
      const winProb = naiveWinProb(homeScore, awayScore, a.period, a.clock ?? '', pregameSpread, pace);
      history.push({
        timestamp: a.timeActual ? new Date(a.timeActual).getTime() : Date.now(),
        winProb,
        scoreDiff: homeScore - awayScore,
        timeRemaining: totalTimeRemainingSeconds(a.period, a.clock ?? ''),
        period: a.period,
      });
      lastHomeScore = homeScore;
      lastAwayScore = awayScore;
    }
  }

  winProbHistory.set(gameId, history);
  return history;
}

function recordSnapshot(
  gameId: string,
  homeScore: number,
  awayScore: number,
  period: number,
  gameClock: string,
  pregameSpread: number,
  pace: number = 100
): number {
  const winProb = naiveWinProb(homeScore, awayScore, period, gameClock, pregameSpread, pace);
  const timeRemaining = totalTimeRemainingSeconds(period, gameClock);
  const scoreDiff = homeScore - awayScore;

  if (!winProbHistory.has(gameId)) winProbHistory.set(gameId, []);
  const history = winProbHistory.get(gameId)!;
  const last = history[history.length - 1];

  if (!last || last.scoreDiff !== scoreDiff || last.timeRemaining !== timeRemaining) {
    history.push({ timestamp: Date.now(), winProb, scoreDiff, timeRemaining, period });
  }

  return winProb;
}

function getHistory(gameId: string): WinProbSnapshot[] {
  return winProbHistory.get(gameId) ?? [];
}
```

## 4. CLV Calculator
```ts
type BetSide = 'home' | 'away' | 'over' | 'under' | 'home_ml' | 'away_ml'

function impliedProb(odds: number): number {
  return odds < 0 ? -odds / (-odds + 100) : 100 / (odds + 100);
}

function deVig(oddsHome: number, oddsAway: number): { home: number; away: number } {
  const raw = impliedProb(oddsHome) + impliedProb(oddsAway);
  return {
    home: impliedProb(oddsHome) / raw,
    away: impliedProb(oddsAway) / raw,
  };
}

function decimalProfit(odds: number): number {
  return odds < 0 ? 100 / -odds : odds / 100;
}

interface CLVResult {
  clvPoints: number;         // positive = beat the close (0 for ML bets)
  edgeVsClose: number;       // as decimal, e.g. 0.032 = 3.2% edge
  breakEvenProb: number;     // what your odds require to break even
  closingNoVigProb: number;  // no-vig probability from closing line
  isML: boolean;
}

/**
 * All spread/total lines must be in HOME perspective before calling (apply adapter below).
 * ML bets: pass yourLine=0, closeLine=0 — only odds are used.
 *
 * home/away/over/under: shows CLV points, hides edge (edge is misleading for line bets)
 * home_ml/away_ml:      shows edge %, hides CLV points (edge is meaningful for ML)
 */
function calcCLV(
  yourSide: BetSide,
  yourLine: number,       // home-perspective spread, raw total, or 0 for ML
  yourOdds: number,
  closeLine: number,      // pregameSpread (spread) | overUnder (total) | 0 (ML)
  closeOddsHome: number,  // spread: home odds  |  total: over odds  |  ML: home ML
  closeOddsAway: number,  // spread: away odds  |  total: under odds |  ML: away ML
): CLVResult {
  const noVig = deVig(closeOddsHome, closeOddsAway);
  const closingNoVigProb =
    yourSide === 'home' || yourSide === 'over' || yourSide === 'home_ml'
      ? noVig.home
      : noVig.away;

  const breakEvenProb = impliedProb(yourOdds);
  const b = decimalProfit(yourOdds);
  const edgeVsClose = closingNoVigProb * (b + 1) - 1;
  const isML = yourSide === 'home_ml' || yourSide === 'away_ml';

  // positive always means "you beat the close"
  // home/under: locked in fewer points to give / higher total ceiling → yourLine - closeLine
  // away/over:  locked in more points received / lower total floor   → closeLine - yourLine
  const clvPoints = isML
    ? 0
    : yourSide === 'home' || yourSide === 'under'
      ? yourLine - closeLine
      : closeLine - yourLine;

  return { clvPoints, edgeVsClose, breakEvenProb, closingNoVigProb, isML };
}
```

## 5. Ticket-to-Model Adapter (spread bets only)
User types the line exactly as it appears on their ticket. Normalize to home perspective before calling `calcCLV`:
```ts
// Spreads only — totals need no normalization (220.5 is 220.5 for both over and under)
const normalizedYourLine = side === 'away' ? -userTypedLine : userTypedLine;
// closeLine (pregameSpread from API) is already home perspective
```

## 6. React Lifecycle
```ts
// LiveWinProbSection useEffect #1 — runs once on mount
useEffect(() => {
  hydrateWinProbHistory(gameId, pregameSpread, pace).then(setChartData);
}, [gameId]);

// LiveWinProbSection useEffect #2 — driven by parent poll (every 15s)
useEffect(() => {
  recordSnapshot(gameId, home.score, away.score, period, gameClock, pregameSpread, pace);
  setChartData([...getHistory(gameId)]);
}, [home.score, away.score, period, gameClock]);
```

## 7. Display Clamping

Always store raw win probability. Clamp only at render time:
```ts
const displayProb = Math.min(0.98, Math.max(0.02, winProb));
```

**Exception — final games (gameStatus === 3):** bypass the clamp and show 0%/100% based on actual score:
```ts
const homeProb = isFinal
  ? (home.score > away.score ? 1 : home.score < away.score ? 0 : 0.5)
  : Math.min(0.98, Math.max(0.02, current.winProb));
```

## Notes for Claude Code

- All functions are pure/stateless except `winProbHistory` map — keep that
  in a module-level singleton
- `pregameSpread` convention throughout: **home perspective, negative = home
  favored** (e.g. `-5.5` means home favored by 5.5)
- OT: sparkline stays active, shows time remaining in current OT period;
  spreadFade is clamped to 0 in OT (pregame line has no predictive value once
  game reaches OT, and the clock reset would spike the sparkline)
- CLV sign convention: **positive always means you beat the close**, for both
  spread and totals; make this explicit in UI copy, not just a +/- sign
- Edge % is only shown for ML bets — for line bets it's misleading because
  closing odds reflect probability at the closing line, not at your line
- `k` is dynamic based on pace (3.0 high pace, 4.0 low pace, 3.5 default) — calibrated so up 3 with 10s left ≈ 96%, up 16 with 36 min left ≈ 77%
- Odds are frozen at closing line for live/final games (API uses persisted
  Supabase odds for gameStatus >= 2) to prevent live line movement from
  contaminating CLV calculations
- NBA CDN PBP endpoint: `cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gameId}.json`
  proxied via Next.js rewrites at `/api/nba-cdn/:path*` to avoid CORS
- Preferred book for odds: **BetOnline** (sharper lines), fallback to first available

## 8. Model Disclaimer

Add a small disclaimer near the win probability display:

```
"Heuristic based on score, time, and spread — not a live market feed"
```
