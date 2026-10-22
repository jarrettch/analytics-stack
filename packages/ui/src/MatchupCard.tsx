"use client"

import { useState, useEffect, useRef } from "react"
import { Badge } from "./components/ui/badge"
import { Separator } from "./components/ui/separator"
import {
  ArrowUp,
  ArrowDown,
  TrendingUp,
  Activity,
  Timer,
  Check,
  X,
  ChevronDown,
  ChevronUp,
  ExternalLink,
} from "lucide-react"
import {
  getHistory,
  hydrateMLBWinProbHistory,
  hydrateESPNWinProbHistory,
  recordMLBSnapshot,
  calcCLV,
  type WinProbSnapshot,
  type BetSide,
  type ESPNBasketballSport,
} from "./lib/winProb"
import type {
  MatchupData,
  NBAMatchupData,
  NFLMatchupData,
  NCAAFMatchupData,
  MLBMatchupData,
  NHLMatchupData,
  WNBAMatchupData,
  NBATeamData,
  NFLTeamData,
  NCAAFTeamData,
  MLBTeamData,
  NHLTeamData,
  BaseTeamData,
} from "./types/sports-types"

// Parse ISO 8601 duration (PT04M00.00S) to display format (4:00)
function parseGameClock(clock: string): string {
  if (!clock) return ""
  const match = clock.match(/PT(\d+)M([\d.]+)S/)
  if (!match || !match[1] || !match[2]) return clock
  const minutes = parseInt(match[1], 10)
  const seconds = Math.floor(parseFloat(match[2]))
  return `${minutes}:${seconds.toString().padStart(2, "0")}`
}

function StatLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
      {children}
    </span>
  )
}

function StatValue({
  children,
  highlight = false,
}: {
  children: React.ReactNode
  highlight?: boolean
}) {
  return (
    <span
      className={`font-mono text-xs tabular-nums ${highlight ? "text-primary" : "text-foreground"}`}
    >
      {children}
    </span>
  )
}

function TeamLogo({
  abbreviation,
  espnSlug,
  sport,
  size = "sm",
}: {
  abbreviation: string
  espnSlug?: string
  sport: string
  size?: "sm" | "md" | "lg"
}) {
  const sizeClasses = {
    sm: "size-5",
    md: "size-8",
    lg: "size-12",
  }

  // Prefer the slug the server picked (NHL emits this; NHL/MLB/NBA all eventually
  // should). Falls back to legacy logic so MLB's "AZ" → "ari" keeps working.
  const slug = espnSlug ?? (abbreviation === "AZ" ? "ari" : abbreviation.toLowerCase())
  // College logos live under "ncaa", keyed by numeric team id (the server's espnSlug).
  const cdnSport = sport === "ncaaf" ? "ncaa" : sport

  return (
    <img
      src={`https://a.espncdn.com/i/teamlogos/${cdnSport}/500/${slug}.png`}
      alt={`${abbreviation} logo`}
      className={`${sizeClasses[size]} mr-1 inline-block`}
      onError={(e) => (e.currentTarget.style.display = "none")}
    />
  )
}

function TeamRow({ team, side, sport }: { team: BaseTeamData; side: "away" | "home"; sport: string }) {
  // College shows the poll rank, not a seed: CFP seeds deliberately differ
  // from rank (top conference champions take seeds 1-5).
  const marker = sport === "ncaaf" ? (team as NCAAFTeamData).rank : team.seed
  return (
    <div className="flex items-center gap-3">
      <TeamLogo abbreviation={team.abbreviation} espnSlug={team.espnSlug} sport={sport} />
      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        {marker ? (
          <span className="font-mono text-[10px] text-muted-foreground">
            {marker}
          </span>
        ) : null}
        <span className="truncate font-sans text-sm font-semibold text-foreground">
          {team.name}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">
          {team.abbreviation}
        </span>
        <Badge
          variant="outline"
          className="ml-auto border-border bg-transparent px-1.5 py-0 font-mono text-[10px] text-muted-foreground"
        >
          {side === "home" ? "HOME" : "AWAY"}
        </Badge>
      </div>
    </div>
  )
}

// Only a W or L streak gets an arrow. Anything else ("N/A", "", "-") is
// missing data, not a losing streak.
function StreakIndicator({ streak }: { streak?: string }) {
  const value = streak?.trim() ?? ""
  const kind = value.startsWith("W") ? "win" : value.startsWith("L") ? "loss" : null
  if (!kind) return <>--</>

  return (
    <span className="inline-flex items-center gap-0.5">
      {kind === "win" ? (
        <ArrowUp className="size-2.5 text-emerald-400" />
      ) : (
        <ArrowDown className="size-2.5 text-red-400" />
      )}
      {value}
    </span>
  )
}

// NBA Stats Row
function NBATeamStats({ team }: { team: NBATeamData }) {
  const last10 = team.last10 || "0-0"
  const conference = team.conference || "NBA"
  const winPct = team.winPct ?? 0.5

  return (
    <div className="grid grid-cols-5 gap-x-3 gap-y-0.5">
      <div className="flex flex-col">
        <StatLabel>Record</StatLabel>
        <StatValue>{team.record}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>Win%</StatLabel>
        <StatValue>{(winPct * 100).toFixed(1)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>L10</StatLabel>
        <StatValue>{last10}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>Streak</StatLabel>
        <StatValue>
          <StreakIndicator streak={team.streak} />
        </StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>Conf</StatLabel>
        <StatValue>{conference}</StatValue>
      </div>
    </div>
  )
}

// NFL Stats Row
function NFLTeamStats({ team }: { team: NFLTeamData }) {
  const fmt = (value: number | null, digits = 1) => value == null ? "--" : value.toFixed(digits)

  return (
    <div className="grid grid-cols-5 gap-x-3 gap-y-0.5">
      <div className="flex flex-col">
        <StatLabel>Record</StatLabel>
        <StatValue>{team.record}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PF/G</StatLabel>
        <StatValue>{fmt(team.pointsFor)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PA/G</StatLabel>
        <StatValue>{fmt(team.pointsAgainst)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>Streak</StatLabel>
        <StatValue>
          <StreakIndicator streak={team.streak} />
        </StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>TO+/-</StatLabel>
        <StatValue>
          <span className={team.turnoverDiff != null && team.turnoverDiff > 0 ? "text-emerald-400" : team.turnoverDiff != null && team.turnoverDiff < 0 ? "text-red-400" : ""}>
            {team.turnoverDiff == null ? "--" : `${team.turnoverDiff > 0 ? "+" : ""}${team.turnoverDiff}`}
          </span>
        </StatValue>
      </div>
    </div>
  )
}

// MLB Stats Row
function MLBTeamStats({ team }: { team: MLBTeamData }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="grid grid-cols-5 gap-x-3 gap-y-0.5">
        <div className="flex flex-col">
          <StatLabel>Record</StatLabel>
          <StatValue>{team.record}</StatValue>
        </div>
        <div className="flex flex-col">
          <StatLabel>RS/G</StatLabel>
          <StatValue>{team.runsScored.toFixed(1)}</StatValue>
        </div>
        <div className="flex flex-col">
          <StatLabel>RA/G</StatLabel>
          <StatValue>{team.runsAllowed.toFixed(1)}</StatValue>
        </div>
        <div className="flex flex-col">
          <StatLabel>ERA</StatLabel>
          <StatValue>{team.era.toFixed(2)}</StatValue>
        </div>
        <div className="flex flex-col">
          <StatLabel>OPS</StatLabel>
          <StatValue>{team.ops.toFixed(3)}</StatValue>
        </div>
      </div>
      {team.probablePitcher && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground">SP:</span>
          <span className="font-mono text-[11px] text-foreground">{team.probablePitcher}</span>
          <span className="font-mono text-[10px] text-muted-foreground">({team.pitcherRecord})</span>
        </div>
      )}
    </div>
  )
}

// NHL Stats Row
function NHLTeamStats({ team }: { team: NHLTeamData }) {
  // None-means-hide: render "--" for missing fields rather than zero, so a real
  // shutout (0) and "no data" don't look identical.
  const fmt = (v: number | null, digits: number) => v == null ? "--" : v.toFixed(digits)
  return (
    <div className="grid grid-cols-5 gap-x-3 gap-y-0.5">
      <div className="flex flex-col">
        <StatLabel>Record</StatLabel>
        <StatValue>{team.record}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>GF/G</StatLabel>
        <StatValue>{fmt(team.goalsFor, 2)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>GA/G</StatLabel>
        <StatValue>{fmt(team.goalsAgainst, 2)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PP%</StatLabel>
        <StatValue>{fmt(team.powerPlayPct, 1)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PK%</StatLabel>
        <StatValue>{fmt(team.penaltyKillPct, 1)}</StatValue>
      </div>
    </div>
  )
}

function RatingBar({
  label,
  awayVal,
  homeVal,
  format = "fixed1",
  higherIsBetter = true,
}: {
  label: string
  awayVal: number
  homeVal: number
  format?: "fixed1" | "fixed0" | "fixed2" | "fixed3" | "pct"
  higherIsBetter?: boolean
}) {
  const awayBetter = higherIsBetter ? awayVal > homeVal : awayVal < homeVal
  const formatVal = (v: number) => {
    switch (format) {
      case "fixed0":
        return v.toFixed(0)
      case "fixed2":
        return v.toFixed(2)
      case "fixed3":
        return v.toFixed(3)
      case "pct":
        return v.toFixed(1) + "%"
      default:
        return v.toFixed(1)
    }
  }

  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
      <div className="text-right">
        <span
          className={`font-mono text-xs tabular-nums ${awayBetter ? "text-primary" : "text-muted-foreground"}`}
        >
          {formatVal(awayVal)}
        </span>
      </div>
      <span className="w-12 text-center text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="text-left">
        <span
          className={`font-mono text-xs tabular-nums ${!awayBetter ? "text-primary" : "text-muted-foreground"}`}
        >
          {formatVal(homeVal)}
        </span>
      </div>
    </div>
  )
}

// NBA Advanced Ratings
function NBARatings({ away, home }: { away: NBATeamData; home: NBATeamData }) {
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2">
      <div className="mb-0.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Ratings Comparison
        </span>
      </div>
      <RatingBar label="ORtg" awayVal={away.offRating} homeVal={home.offRating} />
      <RatingBar label="DRtg" awayVal={away.defRating} homeVal={home.defRating} higherIsBetter={false} />
      <RatingBar label="Pace" awayVal={away.pace} homeVal={home.pace} />
    </div>
  )
}

// NFL Advanced Ratings
function NFLRatings({ away, home }: { away: NFLTeamData; home: NFLTeamData }) {
  const hasYards = away.yardsPG != null && home.yardsPG != null
  const hasYardsAllowed = away.yardsAllowedPG != null && home.yardsAllowedPG != null
  const hasPointDiff = away.pointsFor != null && away.pointsAgainst != null && home.pointsFor != null && home.pointsAgainst != null
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2">
      <div className="mb-0.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Efficiency Comparison
        </span>
      </div>
      {hasYards && <RatingBar label="YPG" awayVal={away.yardsPG as number} homeVal={home.yardsPG as number} format="fixed1" />}
      {hasYardsAllowed && <RatingBar label="YA/G" awayVal={away.yardsAllowedPG as number} homeVal={home.yardsAllowedPG as number} format="fixed1" higherIsBetter={false} />}
      {hasPointDiff && <RatingBar label="Diff" awayVal={(away.pointsFor as number) - (away.pointsAgainst as number)} homeVal={(home.pointsFor as number) - (home.pointsAgainst as number)} format="fixed1" />}
    </div>
  )
}

// MLB Advanced Ratings
function MLBRatings({ away, home }: { away: MLBTeamData; home: MLBTeamData }) {
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2">
      <div className="mb-0.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Stat Comparison
        </span>
      </div>
      <RatingBar label="AVG" awayVal={away.battingAvg} homeVal={home.battingAvg} format="fixed3" />
      <RatingBar label="ERA" awayVal={away.era} homeVal={home.era} format="fixed2" higherIsBetter={false} />
      <RatingBar label="OPS" awayVal={away.ops} homeVal={home.ops} format="fixed3" />
    </div>
  )
}

// NHL Advanced Ratings — each row hides when either side is missing data.
function NHLRatings({ away, home }: { away: NHLTeamData; home: NHLTeamData }) {
  const pair = <T,>(a: T | null, b: T | null): [T, T] | null =>
    a != null && b != null ? [a, b] : null
  const pp = pair(away.powerPlayPct, home.powerPlayPct)
  const pk = pair(away.penaltyKillPct, home.penaltyKillPct)
  const sv = pair(away.savesPct, home.savesPct)
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2">
      <div className="mb-0.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Special Teams & Goaltending
        </span>
      </div>
      {pp && <RatingBar label="PP%" awayVal={pp[0]} homeVal={pp[1]} format="pct" />}
      {pk && <RatingBar label="PK%" awayVal={pk[0]} homeVal={pk[1]} format="pct" />}
      {sv && <RatingBar label="SV%" awayVal={sv[0] * 100} homeVal={sv[1] * 100} format="fixed1" />}
    </div>
  )
}

// Sport-specific stats renderer
function TeamStats({ team, sport }: { team: NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData; sport: string }) {
  switch (sport) {
    case "nba":
    case "wnba":
      return <NBATeamStats team={team as NBATeamData} />
    case "nfl":
    case "ncaaf":
      return <NFLTeamStats team={team as NFLTeamData} />
    case "mlb":
      return <MLBTeamStats team={team as MLBTeamData} />
    case "nhl":
      return <NHLTeamStats team={team as NHLTeamData} />
    default:
      return null
  }
}

// ─── Win Prob Sparkline ──────────────────────────────────────────────────────

// Monotonically increasing elapsed seconds — OT periods continue past 2880
function snapshotElapsed(
  period: number,
  timeRemaining: number,
  regulationSeconds = 2880,
): number {
  if (period <= 4) return regulationSeconds - timeRemaining
  return regulationSeconds + (period - 4) * 300 - timeRemaining
}

function WinProbSparkline({ snapshots, sport = "nba" }: { snapshots: WinProbSnapshot[], sport?: string }) {
  if (snapshots.length < 2) return null

  const W = 300
  const H = 40
  const clamp = (v: number) => Math.min(0.98, Math.max(0.02, v))

  let points: string
  if (sport === "mlb") {
    const maxAtBat = Math.max(...snapshots.map((s) => s.timeRemaining), 1)
    points = snapshots
      .map((s) => {
        const x = (s.timeRemaining / maxAtBat) * W
        const y = (1 - clamp(s.winProb)) * H
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(" ")
  } else {
    const regulationSeconds = sport === "wnba" ? 2400 : 2880
    const maxPeriod = snapshots.reduce((m, s) => Math.max(m, s.period), 4)
    const totalSeconds = maxPeriod <= 4
      ? regulationSeconds
      : regulationSeconds + (maxPeriod - 4) * 300
    points = snapshots
      .map((s) => {
        const x = (
          snapshotElapsed(s.period, s.timeRemaining, regulationSeconds) / totalSeconds
        ) * W
        const y = (1 - clamp(s.winProb)) * H
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(" ")
  }

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      preserveAspectRatio="none"
      style={{ height: "40px" }}
    >
      <line
        x1="0" y1={H / 2} x2={W} y2={H / 2}
        stroke="currentColor" strokeOpacity="0.2"
        strokeWidth="1" strokeDasharray="3,3"
      />
      <polyline
        points={points}
        fill="none"
        stroke="#10b981"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

// ─── Live MLB Win Prob Section ────────────────────────────────────────────────

function LiveMLBWinProbSection({ matchup }: { matchup: MLBMatchupData }) {
  const [chartData, setChartData] = useState<WinProbSnapshot[]>([])
  const hydratedRef = useRef(false)

  useEffect(() => {
    if (hydratedRef.current) return
    hydratedRef.current = true
    hydrateMLBWinProbHistory(matchup.gameId).then((history) => {
      setChartData([...history])
    })
  }, [matchup.gameId])

  // Skip post-final: liveWinProbability can fall back to pregame ~0.5 once the
  // contextMetrics feed stops, which would bend the sparkline toward midline.
  useEffect(() => {
    if (matchup.gameStatus === 3) return
    const winProb = matchup.home.liveWinProbability ?? matchup.home.winProbability
    if (winProb == null) return  // No probability signal — don't bend the sparkline with a fabricated midline.
    const scoreDiff = matchup.home.score - matchup.away.score
    recordMLBSnapshot(matchup.gameId, winProb, scoreDiff, matchup.currentInning)
    setChartData([...getHistory(String(matchup.gameId))])
  }, [matchup.home.score, matchup.away.score, matchup.home.liveWinProbability, matchup.currentInning, matchup.gameStatus]) // eslint-disable-line react-hooks/exhaustive-deps

  const current = chartData[chartData.length - 1]
  if (!current) {
    return (
      <div className="px-3 py-4 flex justify-center">
        <span className="font-mono text-[10px] text-muted-foreground animate-pulse">Loading timeline...</span>
      </div>
    )
  }

  const isFinal = matchup.gameStatus === 3
  const liveProb = matchup.home.liveWinProbability ?? current.winProb
  const homeProb = isFinal
    ? (matchup.home.score > matchup.away.score ? 1 : matchup.home.score < matchup.away.score ? 0 : 0.5)
    : Math.min(0.98, Math.max(0.02, liveProb))
  const awayProb = 1 - homeProb

  return (
    <div className="px-3 py-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {isFinal ? "Win Prob" : "Live Win Prob"}
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">MLB API</span>
      </div>
      <div className="flex items-center gap-2 mb-1.5">
        <span className="w-12 text-right font-mono text-xs font-semibold tabular-nums text-emerald-400">
          {(awayProb * 100).toFixed(1)}%
        </span>
        <div className="flex h-2.5 flex-1 overflow-hidden rounded-sm">
          <div className="bg-emerald-500 transition-all" style={{ width: `${awayProb * 100}%` }} />
          <div className="bg-red-500/80 transition-all" style={{ width: `${homeProb * 100}%` }} />
        </div>
        <span className="w-12 font-mono text-xs font-semibold tabular-nums text-red-400">
          {(homeProb * 100).toFixed(1)}%
        </span>
      </div>
      <div className="mb-2 flex justify-between">
        <span className="font-mono text-[10px] text-muted-foreground">{matchup.away.abbreviation}</span>
        <span className="font-mono text-[10px] text-muted-foreground">{matchup.home.abbreviation}</span>
      </div>
      <WinProbSparkline snapshots={chartData} sport="mlb" />
      <p className="mt-1 font-mono text-[9px] text-muted-foreground/50">
        Live win probability via MLB Stats API
      </p>
    </div>
  )
}

// ─── Live NBA / WNBA Win Prob Section ─────────────────────────────────────────

function LiveESPNWinProbSection({
  matchup,
  sport,
  apiBaseUrl,
}: {
  matchup: NBAMatchupData | WNBAMatchupData
  sport: ESPNBasketballSport
  apiBaseUrl: string
}) {
  const [chartData, setChartData] = useState<WinProbSnapshot[]>([])

  useEffect(() => {
    let cancelled = false
    hydrateESPNWinProbHistory(sport, matchup.gameId, apiBaseUrl).then((history) => {
      if (!cancelled) setChartData([...history])
    })
    return () => {
      cancelled = true
    }
  }, [
    sport,
    matchup.gameId,
    matchup.home.score,
    matchup.away.score,
    matchup.period,
    matchup.gameClock,
    apiBaseUrl,
  ])

  const current = chartData[chartData.length - 1]
  if (!current) return null

  const isFinal = matchup.gameStatus === 3
  const homeProb = Math.min(1, Math.max(0, current.winProb))
  const awayProb = 1 - homeProb

  return (
    <>
      <div className="px-3 py-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {isFinal ? "Win Prob" : "Live Win Prob"}
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">ESPN</span>
        </div>
        <div className="flex items-center gap-2 mb-1.5">
          <span className="w-12 text-right font-mono text-xs font-semibold tabular-nums text-emerald-400">
            {(awayProb * 100).toFixed(1)}%
          </span>
          <div className="flex h-2.5 flex-1 overflow-hidden rounded-sm">
            <div className="bg-emerald-500 transition-all" style={{ width: `${awayProb * 100}%` }} />
            <div className="bg-red-500/80 transition-all" style={{ width: `${homeProb * 100}%` }} />
          </div>
          <span className="w-12 font-mono text-xs font-semibold tabular-nums text-red-400">
            {(homeProb * 100).toFixed(1)}%
          </span>
        </div>
        <div className="mb-2 flex justify-between">
          <span className="font-mono text-[10px] text-muted-foreground">{matchup.away.abbreviation}</span>
          <span className="font-mono text-[10px] text-muted-foreground">{matchup.home.abbreviation}</span>
        </div>
        <WinProbSparkline snapshots={chartData} sport={sport} />
        <p className="mt-1 font-mono text-[9px] text-muted-foreground/50">
          Live win probability via ESPN
        </p>
      </div>
      <Separator className="bg-border" />
    </>
  )
}

// ─── EV Panel (Spread / Total CLV + ML Edge) ─────────────────────────────────

const BET_SIDES: { side: BetSide; label: string }[] = [
  { side: 'home',    label: 'Home'    },
  { side: 'away',    label: 'Away'    },
  { side: 'over',    label: 'Over'    },
  { side: 'under',   label: 'Under'   },
  { side: 'home_ml', label: 'Home ML' },
  { side: 'away_ml', label: 'Away ML' },
]

function EVPanel({ matchup }: { matchup: NBAMatchupData }) {
  const [side, setSide] = useState<BetSide>('home')
  const [lineInput, setLineInput] = useState('')
  const [oddsInput, setOddsInput] = useState('')

  const {
    pregameSpread, spreadOddsHome, spreadOddsAway,
    overUnder, overOdds, underOdds,
    homeMoneyline, awayMoneyline,
    home, away,
  } = matchup

  const isML = side === 'home_ml' || side === 'away_ml'
  const isSpread = side === 'home' || side === 'away'
  const isTotal = side === 'over' || side === 'under'

  // Close line and odds based on bet type
  const closeLine = isSpread ? (pregameSpread ?? 0) : isTotal ? (overUnder ?? 0) : 0
  const closeOddsHome = isSpread
    ? (spreadOddsHome ?? -110)
    : isTotal
      ? (overOdds ?? -110)
      : (homeMoneyline ?? -110)
  const closeOddsAway = isSpread
    ? (spreadOddsAway ?? -110)
    : isTotal
      ? (underOdds ?? -110)
      : (awayMoneyline ?? -110)

  const hasCloseData = isML
    ? homeMoneyline != null && awayMoneyline != null
    : isTotal
      ? overUnder != null && overOdds != null && underOdds != null
      : pregameSpread != null && spreadOddsHome != null && spreadOddsAway != null

  const yourLine = parseFloat(lineInput)
  const yourOdds = parseFloat(oddsInput)
  const canCalc = !isNaN(yourOdds) && oddsInput !== '' && hasCloseData && (isML || (!isNaN(yourLine) && lineInput !== ''))

  // Adapter: normalize away spread to home perspective before calcCLV
  const normalizedLine = side === 'away' ? -yourLine : yourLine

  const result = canCalc
    ? calcCLV(side, normalizedLine, yourOdds, closeLine, closeOddsHome, closeOddsAway)
    : null

  // Display close reference
  const closeDisplay = isML
    ? `${side === 'home_ml' ? home.abbreviation : away.abbreviation} ML: ${(side === 'home_ml' ? homeMoneyline : awayMoneyline) ?? 'N/A'}`
    : isTotal
      ? `O/U ${closeLine.toFixed(1)} (${closeOddsHome > 0 ? '+' : ''}${closeOddsHome} / ${closeOddsAway > 0 ? '+' : ''}${closeOddsAway})`
      : `${closeLine > 0 ? '+' : ''}${closeLine.toFixed(1)} (${closeOddsHome > 0 ? '+' : ''}${closeOddsHome} / ${closeOddsAway > 0 ? '+' : ''}${closeOddsAway})`

  const linePlaceholder = isTotal ? '220.5' : side === 'home' ? '-5.5' : '+5.5'

  return (
    <div className="px-3 py-2 border-t border-border">
      {/* Side selector */}
      <div className="mb-2 flex flex-wrap gap-1">
        {BET_SIDES.map(({ side: s, label }) => (
          <button
            key={s}
            onClick={() => { setSide(s); setLineInput(''); setOddsInput('') }}
            className={`px-2 py-0.5 font-mono text-[10px] uppercase rounded-sm border transition-colors ${
              side === s
                ? 'bg-primary text-primary-foreground border-primary'
                : 'border-border text-muted-foreground hover:text-foreground'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Inputs */}
      <div className="flex gap-2 mb-2">
        {!isML && (
          <div className="flex flex-col flex-1">
            <span className="text-[10px] text-muted-foreground mb-0.5">Your Line</span>
            <input
              type="number"
              step="0.5"
              placeholder={linePlaceholder}
              value={lineInput}
              onChange={(e) => setLineInput(e.target.value)}
              className="bg-secondary border border-border rounded-sm px-2 py-1 font-mono text-xs text-foreground w-full"
            />
          </div>
        )}
        <div className="flex flex-col flex-1">
          <span className="text-[10px] text-muted-foreground mb-0.5">Your Odds</span>
          <input
            type="number"
            step="1"
            placeholder="-110"
            value={oddsInput}
            onChange={(e) => setOddsInput(e.target.value)}
            className="bg-secondary border border-border rounded-sm px-2 py-1 font-mono text-xs text-foreground w-full"
          />
        </div>
      </div>

      {/* Close reference */}
      {hasCloseData && (
        <div className="mb-2 font-mono text-[10px] text-muted-foreground">
          Close: {closeDisplay}
        </div>
      )}
      {!hasCloseData && (
        <div className="mb-2 font-mono text-[10px] text-muted-foreground">No closing odds available</div>
      )}

      {/* Result */}
      {result !== null && (
        <div className="grid grid-cols-3 gap-2 pt-1.5 border-t border-border">
          {result.isML ? (
            // ML: show edge %, no-vig, break-even
            <>
              <div className="flex flex-col">
                <span className="text-[10px] text-muted-foreground">Edge</span>
                <span className={`font-mono text-xs font-semibold ${result.edgeVsClose > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {result.edgeVsClose > 0 ? '+' : ''}{(result.edgeVsClose * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-muted-foreground">No-vig</span>
                <span className="font-mono text-xs text-foreground">
                  {(result.closingNoVigProb * 100).toFixed(1)}%
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-muted-foreground">Break-even</span>
                <span className="font-mono text-xs text-foreground">
                  {(result.breakEvenProb * 100).toFixed(1)}%
                </span>
              </div>
            </>
          ) : (
            // Spread/Total: show CLV points + break-even (hide edge — not accurate for line bets)
            <>
              <div className="flex flex-col col-span-2">
                <span className="text-[10px] text-muted-foreground">CLV</span>
                <span className={`font-mono text-xs font-semibold ${result.clvPoints > 0 ? 'text-emerald-400' : result.clvPoints < 0 ? 'text-red-400' : 'text-foreground'}`}>
                  {result.clvPoints > 0 ? '+' : ''}{result.clvPoints.toFixed(1)} pts
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {result.clvPoints > 0 ? 'Beat close' : result.clvPoints < 0 ? 'Lost to close' : 'Push'}
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-muted-foreground">Break-even</span>
                <span className="font-mono text-xs text-foreground">
                  {(result.breakEvenProb * 100).toFixed(1)}%
                </span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export function MatchupCard({
  matchup,
  apiBaseUrl = "",
}: {
  matchup: MatchupData
  apiBaseUrl?: string
}) {
  const [clvOpen, setClvOpen] = useState(false)

  if (!matchup || !matchup.away || !matchup.home) {
    return null
  }

  const { away, home } = matchup
  const hasWinProb = away.winProbability !== null && home.winProbability !== null
  const awayProbPct = hasWinProb ? (away.winProbability as number) * 100 : 0
  const homeProbPct = hasWinProb ? (home.winProbability as number) * 100 : 0
  const isLiveNBA = matchup.sport === "nba" && (matchup as NBAMatchupData).gameStatus >= 2
  const isLiveWNBA = matchup.sport === "wnba" && (matchup as WNBAMatchupData).gameStatus >= 2
  const isLiveMLB = matchup.sport === "mlb" && (matchup as MLBMatchupData).gameStatus >= 2
  const isLiveDetailGame =
    (matchup.sport === "nba" && (matchup as NBAMatchupData).gameStatus === 2) ||
    (matchup.sport === "nfl" && (matchup as NFLMatchupData).gameStatus === 2) ||
    (matchup.sport === "nhl" && (matchup as NHLMatchupData).gameStatus === 2) ||
    (matchup.sport === "mlb" && (matchup as MLBMatchupData).gameStatus === 2)

  return (
    <div className="w-full max-w-md overflow-hidden rounded-md border border-border bg-card">
      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-border bg-secondary px-3 py-1.5">
        <div className="flex items-center gap-2">
          <Activity className="size-3 text-primary" />
          <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            {matchup.model}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-muted-foreground">
            {matchup.broadcast}
          </span>
          {(matchup.sport === "nfl" || matchup.sport === "ncaaf") && (
            <span className="font-mono text-[10px] text-primary">
              {(matchup as NFLMatchupData | NCAAFMatchupData).weekLabel}
            </span>
          )}
          <span className="font-mono text-[10px] text-muted-foreground">
            {matchup.gameId}
          </span>
          {(matchup.sport === "nba" || matchup.sport === "nfl" || matchup.sport === "nhl" || matchup.sport === "mlb") && (
            <a
              href={`/game/${matchup.gameId}?sport=${matchup.sport}`}
              className={`flex items-center gap-1 rounded-sm px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors ${
                isLiveDetailGame
                  ? "bg-emerald-500/10 text-emerald-400 [filter:drop-shadow(0_0_5px_#34d399)] hover:bg-emerald-500/20 hover:text-emerald-300"
                  : "border border-border bg-transparent text-muted-foreground hover:bg-secondary/70 hover:text-foreground"
              }`}
            >
              Detail
              <ExternalLink className="size-3" />
            </a>
          )}
        </div>
      </div>

      {/* Date / Time / Venue */}
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
        <div className="flex items-center gap-1.5">
          <Timer className="size-3 text-muted-foreground" />
          <span className="font-mono text-[11px] text-foreground">
            {matchup.date}
          </span>
          <span className="font-mono text-[11px] text-primary">
            {matchup.time}
          </span>
        </div>
        <span className="truncate pl-4 font-mono text-[10px] text-muted-foreground">
          {matchup.venue}
        </span>
      </div>

      {/* Teams block */}
      <div className="flex flex-col gap-2 px-3 py-2">
        {/* Away team */}
        <div className="flex flex-col gap-1">
          <TeamRow team={away} side="away" sport={matchup.sport} />
          <TeamStats team={away} sport={matchup.sport} />
        </div>

        <Separator className="bg-border" />

        {/* Home team */}
        <div className="flex flex-col gap-1">
          <TeamRow team={home} side="home" sport={matchup.sport} />
          <TeamStats team={home} sport={matchup.sport} />
        </div>
      </div>

      <Separator className="bg-border" />

      {/* Win Probability Bar — hidden entirely when odds unavailable; no even-split fallback. */}
      {hasWinProb && (
      <div className="px-3 py-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Pregame Win Probability
          </span>
          <TrendingUp className="size-3 text-muted-foreground" />
        </div>
        <div className="flex items-center gap-2">
          <span className="w-12 text-right font-mono text-xs font-semibold tabular-nums text-emerald-400">
            {awayProbPct.toFixed(1)}%
          </span>
          <div className="flex h-2.5 flex-1 overflow-hidden rounded-sm">
            <div
              className="bg-emerald-500 transition-all"
              style={{ width: `${awayProbPct}%` }}
            />
            <div
              className="bg-red-500/80 transition-all"
              style={{ width: `${homeProbPct}%` }}
            />
          </div>
          <span className="w-12 font-mono text-xs font-semibold tabular-nums text-red-400">
            {homeProbPct.toFixed(1)}%
          </span>
        </div>
        <div className="mt-0.5 flex justify-between">
          <span className="font-mono text-[10px] text-muted-foreground">
            {away.abbreviation}
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            {home.abbreviation}
          </span>
        </div>
      </div>
      )}

      <Separator className="bg-border" />

      {/* Score Section - Dynamic based on game status */}
      <div className="px-3 py-2">
        {(matchup.sport === "nba" || matchup.sport === "wnba" || matchup.sport === "nfl" || matchup.sport === "ncaaf" || matchup.sport === "mlb" || matchup.sport === "nhl") && (matchup as NBAMatchupData | WNBAMatchupData | NFLMatchupData | NCAAFMatchupData | MLBMatchupData | NHLMatchupData).gameStatus >= 2 ? (
          // Live or Final game - show actual score
          <>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {(matchup as NBAMatchupData | WNBAMatchupData | NFLMatchupData | NCAAFMatchupData | MLBMatchupData | NHLMatchupData).gameStatus === 3 ? "Final" : "Live"}
              </span>
              {(matchup as NBAMatchupData | WNBAMatchupData | NFLMatchupData | NCAAFMatchupData | MLBMatchupData | NHLMatchupData).gameStatus === 2 && (
                <span className="font-mono text-[10px] text-emerald-400 animate-pulse rounded-sm bg-emerald-500/10 px-0.5 py-0.5 [filter:drop-shadow(0_0_6px_#34d399)]">
                  {matchup.sport === "nba" || matchup.sport === "wnba"
                    ? `Q${(matchup as NBAMatchupData | WNBAMatchupData).period} ${parseGameClock((matchup as NBAMatchupData | WNBAMatchupData).gameClock)}`
                    : matchup.status}
                </span>
              )}
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr] items-center">
              <div className="flex flex-col items-center">
                <span className="font-mono text-[10px] text-muted-foreground">
                  {away.abbreviation}
                </span>
                <span
                  className={`font-mono text-2xl font-bold tabular-nums ${(away as NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData).score > (home as NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData).score ? "text-emerald-400" : "text-foreground"}`}
                >
                  {(away as NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData).score}
                </span>
              </div>
              <span className="px-3 font-mono text-xs text-muted-foreground">
                {" - "}
              </span>
              <div className="flex flex-col items-center">
                <span className="font-mono text-[10px] text-muted-foreground">
                  {home.abbreviation}
                </span>
                <span
                  className={`font-mono text-2xl font-bold tabular-nums ${(home as NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData).score > (away as NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData).score ? "text-emerald-400" : "text-foreground"}`}
                >
                  {(home as NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData).score}
                </span>
              </div>
            </div>
          </>
        ) : (
          // Scheduled game - show projected score
          <>
            <div className="mb-1">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Projected Score
              </span>
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr] items-center">
              <div className="flex flex-col items-center">
                <span className="font-mono text-[10px] text-muted-foreground">
                  {away.abbreviation}
                </span>
                <span
                  className={`font-mono text-2xl font-bold tabular-nums ${away.predictedScore > home.predictedScore ? "text-emerald-400" : "text-foreground"}`}
                >
                  {away.predictedScore}
                </span>
              </div>
              <span className="px-3 font-mono text-xs text-muted-foreground">
                {" - "}
              </span>
              <div className="flex flex-col items-center">
                <span className="font-mono text-[10px] text-muted-foreground">
                  {home.abbreviation}
                </span>
                <span
                  className={`font-mono text-2xl font-bold tabular-nums ${home.predictedScore > away.predictedScore ? "text-emerald-400" : "text-foreground"}`}
                >
                  {home.predictedScore}
                </span>
              </div>
            </div>
          </>
        )}
      </div>

      <Separator className="bg-border" />

      {/* Live win prob — NBA / WNBA (ESPN-sourced) */}
      {(isLiveNBA || isLiveWNBA) && (
        <LiveESPNWinProbSection
          matchup={matchup as NBAMatchupData | WNBAMatchupData}
          sport={isLiveNBA ? "nba" : "wnba"}
          apiBaseUrl={apiBaseUrl}
        />
      )}

      {/* Live win prob — MLB (API-sourced) */}
      {isLiveMLB && (
        <>
          <LiveMLBWinProbSection matchup={matchup as MLBMatchupData} />
          <Separator className="bg-border" />
        </>
      )}

      {/* EV Calculator (all NBA games) */}
      {matchup.sport === "nba" && (
        <>
          <button
            onClick={() => setClvOpen((v) => !v)}
            className="flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-secondary/50 transition-colors"
          >
            <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              EV Calculator
            </span>
            {clvOpen
              ? <ChevronUp className="size-3 text-muted-foreground" />
              : <ChevronDown className="size-3 text-muted-foreground" />
            }
          </button>
          {clvOpen && <EVPanel matchup={matchup as NBAMatchupData} />}
          <Separator className="bg-border" />
        </>
      )}

      {/* Advanced ratings comparison - sport specific */}
      {matchup.sport === "nba" && <NBARatings away={(matchup as NBAMatchupData).away} home={(matchup as NBAMatchupData).home} />}
      {matchup.sport === "wnba" && <NBARatings away={(matchup as WNBAMatchupData).away} home={(matchup as WNBAMatchupData).home} />}
      {matchup.sport === "nfl" && <NFLRatings away={(matchup as NFLMatchupData).away} home={(matchup as NFLMatchupData).home} />}
      {matchup.sport === "ncaaf" && <NFLRatings away={(matchup as NCAAFMatchupData).away} home={(matchup as NCAAFMatchupData).home} />}
      {matchup.sport === "mlb" && <MLBRatings away={(matchup as MLBMatchupData).away} home={(matchup as MLBMatchupData).home} />}
      {matchup.sport === "nhl" && <NHLRatings away={(matchup as NHLMatchupData).away} home={(matchup as NHLMatchupData).home} />}

      {/* Footer: line + O/U */}
      <div className="flex items-center justify-between border-t border-border bg-secondary px-3 py-1.5">
        <div className="flex items-center gap-3">
          {matchup.sport === "mlb" ? (
            <>
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">ML</span>
              <span className="font-mono text-xs font-semibold tabular-nums text-foreground">
                {(() => {
                  const m = matchup as MLBMatchupData
                  const fmt = (v: number | null | undefined) => v != null ? (v > 0 ? `+${v}` : `${v}`) : "N/A"
                  return `${away.abbreviation} ${fmt(m.awayMoneyline)} // ${home.abbreviation} ${fmt(m.homeMoneyline)}`
                })()}
              </span>
            </>
          ) : (
            <>
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Spread</span>
              <span className="font-mono text-xs font-semibold tabular-nums text-foreground flex items-center gap-1">
                {matchup.spreadLine}
                {matchup.spreadResult === true && <Check className="size-3 text-emerald-400" />}
                {matchup.spreadResult === false && <X className="size-3 text-red-400" />}
                {matchup.spreadResult === "push" && (
                  <span className="text-[10px] text-muted-foreground">P</span>
                )}
              </span>
            </>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            O/U
          </span>
          <span className="font-mono text-xs font-semibold tabular-nums text-foreground flex items-center gap-1">
            {matchup.overUnder ? matchup.overUnder.toFixed(1) : "N/A"}
            {matchup.totalResult === "over" && (
              <ArrowUp className="size-3 text-emerald-400" />
            )}
            {matchup.totalResult === "under" && (
              <ArrowDown className="size-3 text-red-400" />
            )}
            {matchup.totalResult === "push" && (
              <span className="text-[10px] text-muted-foreground">P</span>
            )}
          </span>
        </div>
        {matchup.series && (
          <div className="flex items-center gap-3">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Series
            </span>
            <span className="font-mono text-xs font-semibold tabular-nums text-foreground">
              {matchup.series}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}

export function SportsDashboard({
  matchups = [],
  apiBaseUrl = "",
}: {
  matchups: MatchupData[]
  apiBaseUrl?: string
}) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {matchups.map((game) => (
        <MatchupCard key={game.gameId} matchup={game} apiBaseUrl={apiBaseUrl} />
      ))}
    </div>
  )
}
