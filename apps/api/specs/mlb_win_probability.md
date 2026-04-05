# MLB Live Win Probability — Implementation Spec

_Last updated: 2026-03-28_

---

## Data Flow Overview

```
Backend /api/mlb/today (every 15s when live games exist)
  → for live games (gameStatus == 2): fetches /api/v1/game/{gamePk}/contextMetrics
  → extracts homeWinProbability / awayWinProbability (0–100 scale)
  → normalizes to 0–1, attaches to home/away team objects
  → returned as part of MLBMatchupData[]

Frontend (LiveWinProbSection) — on mount
  → hydrateMLBWinProbHistory() — fetches /api/v1/game/{gamePk}/winProbability directly
  → walks play array, seeds snapshot history from homeTeamWinProbability per play
  → uses atBatIndex as x-axis proxy for game progression

Frontend (LiveWinProbSection) — on poll update (every 15s)
  → receives updated home.winProbability from backend contextMetrics
  → calls recordSnapshot() to append current value
  → sparkline continues from seeded history
```

---

## 1. Backend Changes (`mlb.py`)

For each live game (`gameStatus == 2`), fire an additional async request alongside existing fetches:

```python
# endpoint
GET https://statsapi.mlb.com/api/v1/game/{gamePk}/contextMetrics

# relevant fields in response
{
  "homeWinProbability": 95.0,   # 0–100 scale
  "awayWinProbability": 5.0
}
```

- Normalize to 0–1 before attaching to team objects: `homeWinProbability / 100`
- If the call fails or `homeWinProbability` is null/missing: fall back to de-vigged moneyline from persisted Supabase odds (same `deVig()` logic used for CLV). Only use `0.5` if odds are also unavailable.
- Use the same 20s live cache TTL as the rest of live game data
- For non-live games (`gameStatus != 2`), omit the call entirely — set `winProbability: 0.5` as a neutral default

---

## 2. Type Changes (`sports-types.ts`)

`BaseTeamData` already has `winProbability: number`. Confirm `MLBTeamData` inherits it (or add it). No new fields needed.

---

## 3. Frontend — History Hydration

Mirror the NBA `hydrateWinProbHistory` pattern. Add a new function in `winProb.ts` (or a separate `mlbWinProb.ts`):

```ts
// Fetches full play-by-play win probability history from MLB Stats API
// Call once on mount when a live MLB game is detected
async function hydrateMLBWinProbHistory(
  gamePk: string | number
): Promise<WinProbSnapshot[]> {
  const url = `/api/mlb-stats/game/${gamePk}/winProbability`; // proxied via Next.js rewrites
  const res = await fetch(url);
  if (!res.ok) return [];
  const plays: MLBWinProbPlay[] = await res.json();
  if (!plays.length) return [];
  const history: WinProbSnapshot[] = [];

  for (const play of plays) {
    history.push({
      timestamp: Date.now(),
      winProb: play.homeTeamWinProbability / 100,
      scoreDiff: play.homeScore - play.awayScore,
      timeRemaining: play.atBatIndex, // x-axis unit for MLB sparkline
      period: play.about?.inning ?? 0,
    });
  }

  winProbHistory.set(String(gamePk), history);
  return history;
}
```

### MLB play shape (relevant fields only)

```ts
interface MLBWinProbPlay {
  atBatIndex: number;
  homeTeamWinProbability: number;   // 0–100
  awayTeamWinProbability: number;
  homeTeamWinProbabilityAdded: number;
  homeScore: number;
  awayScore: number;
  about?: {
    inning: number;
    halfInning: "top" | "bottom";
  };
}
```

---

## 4. Frontend — Polling Continuation

After hydration, each poll appends the current value from `contextMetrics` (returned by the backend via `home.winProbability`):

```ts
// LiveWinProbSection useEffect #1 — runs once on mount (MLB live only)
useEffect(() => {
  hydrateMLBWinProbHistory(gamePk).then(setChartData);
}, [gamePk]);

// LiveWinProbSection useEffect #2 — driven by parent poll (every 15s)
useEffect(() => {
  // For MLB: winProb comes directly from backend, no local calculation
  const winProb = home.winProbability; // already 0–1, from contextMetrics
  const snapshot: WinProbSnapshot = {
    timestamp: Date.now(),
    winProb,
    scoreDiff: home.score - away.score,
    timeRemaining: maxAtBatIndex + pollCount, // continues x-axis from where hydration left off
    // pollCount is keyed per gamePk (Map<string, number> alongside winProbHistory) — not a global counter
    period: currentInning,
  };
  appendSnapshot(gamePk, snapshot);
  setChartData([...getHistory(String(gamePk))]);
}, [home.score, away.score, currentInning]);

// Pass sport prop so WinProbSparkline uses the correct x-axis path
// <WinProbSparkline snapshots={chartData} sport="mlb" />
```

---

## 5. Frontend Polling (`page.tsx`)

MLB currently has no live polling. Add it:

```ts
if (activeSport === "nba" || (activeSport === "mlb" && hasLiveMlbGames)) {
  pollInterval = setInterval(() => fetchData(false), 15000);
}
```

`hasLiveMlbGames` — derived the same way as `hasLiveGames` for NBA: check if any game in the MLB response has `gameStatus === 2`.

---

## 6. Display

Reuse `LiveWinProbSection` as-is. The component is not NBA-specific — it just needs `winProbability` on the team objects and a populated snapshot history. Remove any sport guard if one exists.

Display clamping: same as NBA — clamp to `[0.02, 0.98]` at render time, bypass for final games.

**Disclaimer:** Replace NBA-specific disclaimer with a sport-appropriate one:

```
"Live win probability via MLB Stats API"
```

---

## 7. X-Axis Normalization

The win probability values themselves come from the MLB API — no local model needed. The x-axis concern is purely about drawing the sparkline correctly.

The existing `WinProbSparkline` computes x position via `snapshotElapsed(period, timeRemaining)`, which assumes NBA quarters and a countdown clock. This is NBA-specific and will produce nonsense for baseball.

**Fix — two parts:**

**1. Store a monotonic index in `timeRemaining` across both phases:**
- Hydration: `timeRemaining = play.atBatIndex`
- Polling: `timeRemaining = maxAtBatIndex + pollCount` (increment a module-level counter per appended snapshot, keyed by `gamePk`)

This gives a single consistent unit (at-bat index) across the full sparkline.

**2. Update `WinProbSparkline` to handle MLB:**

Pass a `sport` prop. For MLB, skip `snapshotElapsed` and normalize x directly:

```ts
const maxT = Math.max(...snapshots.map(s => s.timeRemaining))
const x = maxT > 0 ? (s.timeRemaining / maxT) * W : 0
```

For NBA, keep the existing `snapshotElapsed` path unchanged.

---

## Notes

- MLB win probability is **API-sourced**, not heuristic — no local model needed, no spread/pace inputs
- `gamePk` is the MLB Stats API integer game ID (e.g. `824213`) — already present in `MLBMatchupData`
- Add a Next.js rewrite proxy at `/api/mlb-stats/:path*` → `https://statsapi.mlb.com/api/v1/:path*` preemptively — same pattern as `/api/nba-cdn/:path*`. Avoids a CORS debugging session if the browser rejects the direct call.
- Default `winProbability: 0.5` for non-live games so the bar renders as 50/50 (neutral) instead of breaking
- No sparkline shown for scheduled/final games — same behavior as NBA
