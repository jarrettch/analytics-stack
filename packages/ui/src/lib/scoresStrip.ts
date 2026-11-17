import type { MatchupData, Sport } from "../types/sports-types"

/** Order sports appear in the strip. */
export const STRIP_SPORTS: Sport[] = ["nba", "wnba", "nfl", "mlb", "nhl", "ncaaf", "ncaab"]

/** Sports with a game detail page to link to. */
const DETAIL_SPORTS: ReadonlySet<Sport> = new Set(["nba", "nfl", "nhl", "mlb"])

export type StripGameState = "live" | "upcoming" | "final"

export interface StripTeam {
  abbreviation: string
  score: number
  /** Poll ranking, college only. */
  rank: number | null
}

export interface StripGame {
  key: string
  sport: Sport
  state: StripGameState
  /** Clock while live, start time before, "Final" after. */
  label: string
  away: StripTeam
  home: StripTeam
  href: string | null
}

const STATE_ORDER: Record<StripGameState, number> = { live: 0, upcoming: 1, final: 2 }

function stateOf(gameStatus: number): StripGameState {
  return gameStatus === 2 ? "live" : gameStatus === 3 ? "final" : "upcoming"
}

// The NBA feeds send the clock as an ISO 8601 duration ("PT03M16.00S") and a
// generic "In Progress" status, so the live label is built from the parts.
function liveLabel(matchup: MatchupData): string {
  const clock = "gameClock" in matchup ? matchup.gameClock : null
  const iso = typeof clock === "string" ? clock.match(/^PT(\d+)M([\d.]+)S$/) : null
  if (iso && "period" in matchup && matchup.period) {
    const seconds = Math.floor(Number(iso[2])).toString().padStart(2, "0")
    const period = matchup.period > 4 ? `OT${matchup.period > 5 ? matchup.period - 4 : ""}` : `Q${matchup.period}`
    return `${period} ${Number(iso[1])}:${seconds}`
  }
  return matchup.status || "Live"
}

function stripTeam(team: MatchupData["away"]): StripTeam {
  const rank = "rank" in team ? team.rank : null
  return { abbreviation: team.abbreviation, score: team.score ?? 0, rank: rank ?? null }
}

/**
 * One sport's slate as strip entries: live games first, then upcoming, then final.
 * College slates run to dozens of games, so only those with a ranked team are kept.
 */
export function toStripGames(sport: Sport, matchups: MatchupData[]): StripGame[] {
  const college = sport === "ncaaf" || sport === "ncaab"
  return matchups
    .filter((matchup) => !college || stripTeam(matchup.away).rank != null || stripTeam(matchup.home).rank != null)
    .map((matchup): StripGame => {
      const state = stateOf(matchup.gameStatus)
      return {
        key: `${sport}-${matchup.gameId}`,
        sport,
        state,
        label: state === "live" ? liveLabel(matchup) : state === "final" ? matchup.status || "Final" : matchup.time || matchup.status,
        away: stripTeam(matchup.away),
        home: stripTeam(matchup.home),
        href: DETAIL_SPORTS.has(sport) ? `/game/${encodeURIComponent(matchup.gameId)}?sport=${sport}` : null,
      }
    })
    // Array sort is stable, so slate order holds within each state.
    .sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state])
}
