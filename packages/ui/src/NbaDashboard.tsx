import { Badge } from "./components/ui/badge"
import { Separator } from "./components/ui/separator"
import {
  ArrowUp,
  ArrowDown,
  TrendingUp,
  Activity,
  Timer,
} from "lucide-react"

export interface TeamData {
  name: string
  abbreviation: string
  record: string
  winPct: number
  seed: number
  conference: string
  streak: string
  last10: string
  predictedScore: number
  winProbability: number
  offRating: number
  defRating: number
  pace: number
}

export interface MatchupData {
  gameId: string
  date: string
  time: string
  venue: string
  broadcast: string
  series?: string
  away: TeamData
  home: TeamData
  spreadLine: string
  overUnder: number
  model: string
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

function TeamRow({ team, side }: { team: TeamData; side: "away" | "home" }) {
  // Defensive streak check
  const streak = team.streak || "N/A";
  const isPositiveStreak = streak !== "N/A" && streak.startsWith("W")

  return (
    <div className="flex items-center gap-3">
      {/* Seed + Name */}
      <img 
        src={`https://a.espncdn.com/i/teamlogos/nba/500/${team.abbreviation.toLowerCase()}.png`} 
        alt="" 
        className="size-5 mr-1 inline-block"
        onError={(e) => (e.currentTarget.style.display = 'none')} 
        />
      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="font-mono text-[10px] text-muted-foreground">
          {team.seed}
        </span>
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

function TeamStats({ team }: { team: TeamData }) {
  // Use local constants instead of mutating props
  const last10 = team.last10 || "0-0";
  const conference = team.conference || "NBA";
  const streak = team.streak || "N/A";
  const winPct = team.winPct ?? 0.5;

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
          <span className="inline-flex items-center gap-0.5">
            {streak.startsWith("W") ? (
              <ArrowUp className="size-2.5 text-emerald-400" />
            ) : (
              <ArrowDown className="size-2.5 text-red-400" />
            )}
            {streak}
          </span>
        </StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>Conf</StatLabel>
        <StatValue>{conference}</StatValue>
      </div>
    </div>
  )
}

function RatingBar({
  label,
  awayVal,
  homeVal,
  format = "fixed1",
}: {
  label: string
  awayVal: number
  homeVal: number
  format?: "fixed1" | "fixed0"
}) {
  const awayBetter = awayVal > homeVal
  const formatVal = (v: number) =>
    format === "fixed1" ? v.toFixed(1) : v.toFixed(0)

  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
      <div className="text-right">
        <span
          className={`font-mono text-xs tabular-nums ${awayBetter ? "text-primary" : "text-muted-foreground"}`}
        >
          {formatVal(awayVal)}
        </span>
      </div>
      <span className="text-center text-[10px] uppercase tracking-wider text-muted-foreground">
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

export function MatchupCard({ matchup }: { matchup: MatchupData }) {
  if (!matchup || !matchup.away || !matchup.home) {
    return null; 
  }

  console.log("Dashboard received:", matchup);
  
  const { away, home } = matchup;
  const awayProbPct = (away.winProbability ?? 0.5) * 100
  const homeProbPct = (home.winProbability ?? 0.5) * 100

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
          <span className="font-mono text-[10px] text-muted-foreground">
            {matchup.gameId}
          </span>
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
          <TeamRow team={away} side="away" />
          <TeamStats team={away} />
        </div>

        <Separator className="bg-border" />

        {/* Home team */}
        <div className="flex flex-col gap-1">
          <TeamRow team={home} side="home" />
          <TeamStats team={home} />
        </div>
      </div>

      <Separator className="bg-border" />

      {/* Win Probability Bar */}
      <div className="px-3 py-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Win Probability
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

      <Separator className="bg-border" />

      {/* Predicted Score */}
      <div className="px-3 py-2">
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
      </div>

      <Separator className="bg-border" />

      {/* Advanced ratings comparison */}
      <div className="flex flex-col gap-0.5 px-3 py-2">
        <div className="mb-0.5">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Ratings Comparison
          </span>
        </div>
        <RatingBar label="ORtg" awayVal={away.offRating} homeVal={home.offRating} />
        <RatingBar label="DRtg" awayVal={away.defRating} homeVal={home.defRating} />
        <RatingBar label="Pace" awayVal={away.pace} homeVal={home.pace} format="fixed1" />
      </div>

      {/* Footer: line + O/U */}
      <div className="flex items-center justify-between border-t border-border bg-secondary px-3 py-1.5">
        <div className="flex items-center gap-3">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Spread
          </span>
          <span className="font-mono text-xs font-semibold tabular-nums text-foreground">
            {matchup.spreadLine}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            O/U
          </span>
          <span className="font-mono text-xs font-semibold tabular-nums text-foreground">
            {(matchup.overUnder ?? 0).toFixed(1)}
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

export function NbaDashboard({ matchups = [] }: { matchups: MatchupData[] }) {
    return (
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        {matchups.map((game) => (
          <MatchupCard key={game.id || game.gameId} matchup={game} />
        ))}
      </div>
    );
  }



// export const NbaDashboard = MatchupCard
