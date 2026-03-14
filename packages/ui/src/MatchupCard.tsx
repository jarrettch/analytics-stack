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
} from "lucide-react"
import type {
  MatchupData,
  NBAMatchupData,
  NFLMatchupData,
  MLBMatchupData,
  NHLMatchupData,
  NBATeamData,
  NFLTeamData,
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
  sport,
  size = "sm",
}: {
  abbreviation: string
  sport: string
  size?: "sm" | "md" | "lg"
}) {
  const sizeClasses = {
    sm: "size-5",
    md: "size-8",
    lg: "size-12",
  }

  return (
    <img
      src={`https://a.espncdn.com/i/teamlogos/${sport}/500/${abbreviation.toLowerCase()}.png`}
      alt={`${abbreviation} logo`}
      className={`${sizeClasses[size]} mr-1 inline-block`}
      onError={(e) => (e.currentTarget.style.display = "none")}
    />
  )
}

function TeamRow({ team, side, sport }: { team: BaseTeamData; side: "away" | "home"; sport: string }) {
  return (
    <div className="flex items-center gap-3">
      <TeamLogo abbreviation={team.abbreviation} sport={sport} />
      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        {team.seed && (
          <span className="font-mono text-[10px] text-muted-foreground">
            {team.seed}
          </span>
        )}
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

// NBA Stats Row
function NBATeamStats({ team }: { team: NBATeamData }) {
  const last10 = team.last10 || "0-0"
  const conference = team.conference || "NBA"
  const streak = team.streak || "N/A"
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

// NFL Stats Row
function NFLTeamStats({ team }: { team: NFLTeamData }) {
  const streak = team.streak || "N/A"

  return (
    <div className="grid grid-cols-5 gap-x-3 gap-y-0.5">
      <div className="flex flex-col">
        <StatLabel>Record</StatLabel>
        <StatValue>{team.record}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PF/G</StatLabel>
        <StatValue>{team.pointsFor.toFixed(1)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PA/G</StatLabel>
        <StatValue>{team.pointsAgainst.toFixed(1)}</StatValue>
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
        <StatLabel>TO+/-</StatLabel>
        <StatValue>
          <span className={team.turnoverDiff > 0 ? "text-emerald-400" : team.turnoverDiff < 0 ? "text-red-400" : ""}>
            {team.turnoverDiff > 0 ? "+" : ""}{team.turnoverDiff}
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
  return (
    <div className="grid grid-cols-5 gap-x-3 gap-y-0.5">
      <div className="flex flex-col">
        <StatLabel>Record</StatLabel>
        <StatValue>{team.record}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>GF/G</StatLabel>
        <StatValue>{team.goalsFor.toFixed(2)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>GA/G</StatLabel>
        <StatValue>{team.goalsAgainst.toFixed(2)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PP%</StatLabel>
        <StatValue>{team.powerPlayPct.toFixed(1)}</StatValue>
      </div>
      <div className="flex flex-col">
        <StatLabel>PK%</StatLabel>
        <StatValue>{team.penaltyKillPct.toFixed(1)}</StatValue>
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
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2">
      <div className="mb-0.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Efficiency Comparison
        </span>
      </div>
      <RatingBar label="YPG" awayVal={away.yardsPG} homeVal={home.yardsPG} format="fixed1" />
      <RatingBar label="YA/G" awayVal={away.yardsAllowedPG} homeVal={home.yardsAllowedPG} format="fixed1" higherIsBetter={false} />
      <RatingBar label="Diff" awayVal={away.pointsFor - away.pointsAgainst} homeVal={home.pointsFor - home.pointsAgainst} format="fixed1" />
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

// NHL Advanced Ratings
function NHLRatings({ away, home }: { away: NHLTeamData; home: NHLTeamData }) {
  return (
    <div className="flex flex-col gap-0.5 px-3 py-2">
      <div className="mb-0.5">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Special Teams & Goaltending
        </span>
      </div>
      <RatingBar label="PP%" awayVal={away.powerPlayPct} homeVal={home.powerPlayPct} format="pct" />
      <RatingBar label="PK%" awayVal={away.penaltyKillPct} homeVal={home.penaltyKillPct} format="pct" />
      <RatingBar label="SV%" awayVal={away.savesPct * 100} homeVal={home.savesPct * 100} format="fixed1" />
    </div>
  )
}

// Sport-specific stats renderer
function TeamStats({ team, sport }: { team: NBATeamData | NFLTeamData | MLBTeamData | NHLTeamData; sport: string }) {
  switch (sport) {
    case "nba":
      return <NBATeamStats team={team as NBATeamData} />
    case "nfl":
      return <NFLTeamStats team={team as NFLTeamData} />
    case "mlb":
      return <MLBTeamStats team={team as MLBTeamData} />
    case "nhl":
      return <NHLTeamStats team={team as NHLTeamData} />
    default:
      return null
  }
}

export function MatchupCard({ matchup }: { matchup: MatchupData }) {
  if (!matchup || !matchup.away || !matchup.home) {
    return null
  }

  const { away, home } = matchup
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
          {matchup.sport === "nfl" && (
            <span className="font-mono text-[10px] text-primary">
              WK{(matchup as NFLMatchupData).week}
            </span>
          )}
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

      {/* Score Section - Dynamic based on game status */}
      <div className="px-3 py-2">
        {matchup.sport === "nba" && (matchup as NBAMatchupData).gameStatus >= 2 ? (
          // Live or Final game - show actual score
          <>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {(matchup as NBAMatchupData).gameStatus === 3 ? "Final" : "Live"}
              </span>
              {(matchup as NBAMatchupData).gameStatus === 2 && (
                <span className="font-mono text-[10px] text-primary animate-pulse">
                  Q{(matchup as NBAMatchupData).period} {parseGameClock((matchup as NBAMatchupData).gameClock)}
                </span>
              )}
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr] items-center">
              <div className="flex flex-col items-center">
                <span className="font-mono text-[10px] text-muted-foreground">
                  {away.abbreviation}
                </span>
                <span
                  className={`font-mono text-2xl font-bold tabular-nums ${(away as NBATeamData).score > (home as NBATeamData).score ? "text-emerald-400" : "text-foreground"}`}
                >
                  {(away as NBATeamData).score}
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
                  className={`font-mono text-2xl font-bold tabular-nums ${(home as NBATeamData).score > (away as NBATeamData).score ? "text-emerald-400" : "text-foreground"}`}
                >
                  {(home as NBATeamData).score}
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

      {/* Advanced ratings comparison - sport specific */}
      {matchup.sport === "nba" && <NBARatings away={(matchup as NBAMatchupData).away} home={(matchup as NBAMatchupData).home} />}
      {matchup.sport === "nfl" && <NFLRatings away={(matchup as NFLMatchupData).away} home={(matchup as NFLMatchupData).home} />}
      {matchup.sport === "mlb" && <MLBRatings away={(matchup as MLBMatchupData).away} home={(matchup as MLBMatchupData).home} />}
      {matchup.sport === "nhl" && <NHLRatings away={(matchup as NHLMatchupData).away} home={(matchup as NHLMatchupData).home} />}

      {/* Footer: line + O/U */}
      <div className="flex items-center justify-between border-t border-border bg-secondary px-3 py-1.5">
        <div className="flex items-center gap-3">
          <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
            Spread
          </span>
          <span className="font-mono text-xs font-semibold tabular-nums text-foreground flex items-center gap-1">
            {matchup.spreadLine}
            {matchup.spreadResult === true && (
              <Check className="size-3 text-emerald-400" />
            )}
            {matchup.spreadResult === false && (
              <X className="size-3 text-red-400" />
            )}
          </span>
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

export function SportsDashboard({ matchups = [] }: { matchups: MatchupData[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {matchups.map((game) => (
        <MatchupCard key={game.gameId} matchup={game} />
      ))}
    </div>
  )
}
