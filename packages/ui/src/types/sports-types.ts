export type Sport = "nba" | "nfl" | "mlb" | "nhl" | "wnba" | "ncaaf" | "ncaab"

export interface BaseTeamData {
  name: string
  abbreviation: string
  espnSlug?: string  // Optional override for ESPN CDN team-logo slug; falls back to abbreviation.toLowerCase()
  record: string
  winPct: number
  seed?: number
  division?: string
  conference: string
  streak: string
  predictedScore: number | null
  winProbability: number | null  // null when no odds available — render rule: hide the bar, don't render an even split
}

// NBA-specific team data
export interface NBATeamData extends BaseTeamData {
  last10: string
  score: number        // Live/final score
  offRating: number
  defRating: number
  pace: number
}

// WNBA reuses the NBA team shape (off/def rating, pace, last10, score).
export type WNBATeamData = NBATeamData

// NFL-specific team data
export interface NFLTeamData extends BaseTeamData {
  score: number
  pointsFor: number | null
  pointsAgainst: number | null
  yardsPG: number | null
  yardsAllowedPG: number | null
  turnoverDiff: number | null
}

// NCAAF adds the two fields the conference and ranked filters key on.
export interface NCAAFTeamData extends BaseTeamData {
  score: number
  pointsFor: number | null
  pointsAgainst: number | null
  yardsPG: number | null
  yardsAllowedPG: number | null
  turnoverDiff: number | null
  rank: number | null        // curatedRank.current, normalized: 99 -> null
  conferenceId: string       // ESPN team.conferenceId
}

// Men's college basketball uses standings averages, not NBA efficiency ratings.
export interface NCAABTeamData extends BaseTeamData {
  score: number
  pointsFor: number | null
  pointsAgainst: number | null
  rank: number | null
  conferenceId: string
}

// MLB-specific team data
export interface MLBTeamData extends BaseTeamData {
  score: number        // Live/final score
  runsScored: number
  runsAllowed: number
  era: number
  battingAvg: number
  ops: number
  probablePitcher?: string
  pitcherRecord?: string
  liveWinProbability?: number  // from contextMetrics, only set for gameStatus === 2
}

// NHL-specific team data
export interface NHLTeamData extends BaseTeamData {
  score: number             // Live/final score
  goalsFor: number | null   // None when team-summary data is missing
  goalsAgainst: number | null
  powerPlayPct: number | null
  penaltyKillPct: number | null
  savesPct: number | null
}

export interface BaseMatchupData {
  gameId: string
  status: string       // Game status text (e.g., "7:30 PM ET", "Final", "Q3 5:23")
  date: string
  time: string
  venue: string
  broadcast: string
  series?: string
  spreadLine: string
  spreadResult?: boolean | "push" | null  // Result for the displayed favorite
  overUnder?: number | null      // Market total from odds API
  totalResult?: "over" | "under" | "push" | null  // Result vs O/U line
  projectedTotal?: number | null // Model's projected total
  model: string
}

export interface NBAMatchupData extends BaseMatchupData {
  sport: "nba"
  gameStatus: number   // 1=scheduled, 2=live, 3=final
  period: number
  gameClock: string
  pregameSpread?: number | null   // home perspective, negative = home favored
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
  away: NBATeamData
  home: NBATeamData
}

// WNBA mirrors the NBA matchup shape (quarter-based clock, same odds fields).
export interface WNBAMatchupData extends BaseMatchupData {
  sport: "wnba"
  gameStatus: number   // 1=scheduled, 2=live, 3=final
  period: number
  gameClock: string
  seriesGameNumber?: string | number | null  // playoff context, null in the regular season
  pregameSpread?: number | null   // home perspective, negative = home favored
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
  away: WNBATeamData
  home: WNBATeamData
}

export interface NFLMatchupData extends BaseMatchupData {
  sport: "nfl"
  gameStatus: number
  period: number
  gameClock: string | null
  away: NFLTeamData
  home: NFLTeamData
  week: number
  weekLabel: string
  seasonType: string
  statsSeason: number
  pregameSpread?: number | null
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
}

// Declared outright: NFLMatchupData pins sport to "nfl" and its teams to
// NFLTeamData, and an extends clause can't narrow either.
export interface NCAAFMatchupData extends BaseMatchupData {
  sport: "ncaaf"
  slateDate?: string
  gameStatus: number
  period: number
  gameClock: string | null
  away: NCAAFTeamData
  home: NCAAFTeamData
  week: number
  weekLabel: string
  seasonType: string
  statsSeason: number
  pregameSpread?: number | null
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
}

export interface NCAABMatchupData extends BaseMatchupData {
  sport: "ncaab"
  slateDate?: string
  gameStatus: number
  period: number
  gameClock: string | null
  away: NCAABTeamData
  home: NCAABTeamData
  seasonType: string
  statsSeason: number
  pregameSpread?: number | null
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
}

export interface MLBMatchupData extends BaseMatchupData {
  sport: "mlb"
  gameNumber?: number
  gameStatus: number           // 1=scheduled, 2=live, 3=final
  currentInning: number        // 0 when not started
  pregameSpread?: number | null
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
  away: MLBTeamData
  home: MLBTeamData
}

export interface NHLMatchupData extends BaseMatchupData {
  sport: "nhl"
  gameStatus: number             // 1=scheduled, 2=live, 3=final
  period: number | null          // null pre-game; 1–3 REG, 4 OT, 5 SO when live/final
  gameClock: string | null       // "mm:ss" when live REG/OT; null in pre-game, intermission, SO, and final
  pregameSpread?: number | null
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
  seriesGameNumber: number | null  // Playoff context, populated by a later feature; v1 emits null.
  seriesStatus: string | null
  away: NHLTeamData
  home: NHLTeamData
}

export type MatchupData =
  | NBAMatchupData | NFLMatchupData | MLBMatchupData
  | NHLMatchupData | WNBAMatchupData | NCAAFMatchupData | NCAABMatchupData

export const SPORT_LABELS: Record<Sport, string> = {
  nba: "NBA",
  nfl: "NFL",
  mlb: "MLB",
  nhl: "NHL",
  wnba: "WNBA",
  ncaaf: "NCAAF",
  ncaab: "NCAAB",
}

export const SPORT_MODELS: Record<Sport, string> = {
  nba: "EV-MODEL-V1",
  nfl: "MARKET + TEAM STATS",
  mlb: "PECOTA v2.8",
  nhl: "xG v5.0",
  wnba: "EV-MODEL-V1",
  ncaaf: "MARKET (DK)",
  ncaab: "MARKET (DK)",
}
