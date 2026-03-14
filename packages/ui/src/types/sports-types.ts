export type Sport = "nba" | "nfl" | "mlb" | "nhl"

export interface BaseTeamData {
  name: string
  abbreviation: string
  record: string
  winPct: number
  seed?: number
  division?: string
  conference: string
  streak: string
  predictedScore: number
  winProbability: number
}

// NBA-specific team data
export interface NBATeamData extends BaseTeamData {
  last10: string
  score: number        // Live/final score
  offRating: number
  defRating: number
  pace: number
}

// NFL-specific team data
export interface NFLTeamData extends BaseTeamData {
  pointsFor: number
  pointsAgainst: number
  yardsPG: number
  yardsAllowedPG: number
  turnoverDiff: number
}

// MLB-specific team data
export interface MLBTeamData extends BaseTeamData {
  runsScored: number
  runsAllowed: number
  era: number
  battingAvg: number
  ops: number
  probablePitcher?: string
  pitcherRecord?: string
}

// NHL-specific team data
export interface NHLTeamData extends BaseTeamData {
  goalsFor: number
  goalsAgainst: number
  powerPlayPct: number
  penaltyKillPct: number
  savesPct: number
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
  spreadResult?: boolean | null  // True = favorite covered, False = didn't cover, null = not final
  overUnder?: number | null      // Market total from odds API
  totalResult?: "over" | "under" | "push" | null  // Result vs O/U line
  projectedTotal?: number        // Model's projected total
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

export interface NFLMatchupData extends BaseMatchupData {
  sport: "nfl"
  away: NFLTeamData
  home: NFLTeamData
  week: number
}

export interface MLBMatchupData extends BaseMatchupData {
  sport: "mlb"
  away: MLBTeamData
  home: MLBTeamData
  gameNumber?: number
}

export interface NHLMatchupData extends BaseMatchupData {
  sport: "nhl"
  away: NHLTeamData
  home: NHLTeamData
}

export type MatchupData = NBAMatchupData | NFLMatchupData | MLBMatchupData | NHLMatchupData

export const SPORT_LABELS: Record<Sport, string> = {
  nba: "NBA",
  nfl: "NFL",
  mlb: "MLB",
  nhl: "NHL",
}

export const SPORT_MODELS: Record<Sport, string> = {
  nba: "EV-MODEL-V1",
  nfl: "DVOA v3.1",
  mlb: "PECOTA v2.8",
  nhl: "xG v5.0",
}
