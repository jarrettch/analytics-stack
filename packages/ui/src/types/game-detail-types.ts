import type { NBAMatchupData, NHLMatchupData } from "./sports-types"

export type GameDetailStatus = "preview" | "live" | "final"

export interface PlayByPlayEvent {
  id: string
  eventId?: string
  sequence?: number
  clock: string
  quarter: number | string
  team: string | null
  event: string
  awayScore: number
  homeScore: number
  winProbAway?: number
  winProbHome?: number
  sourceTimestamp?: string
}

export interface WinProbPoint {
  x: number
  awayProb: number
  homeProb: number
  label?: string
}

export interface NBAPlayerBoxScore {
  name: string
  position: string
  minutes: string
  points: number
  rebounds: number
  assists: number
  steals: number
  blocks: number
  turnovers: number
  fouls: number
  fgm: number
  fga: number
  threePm: number
  threePa: number
  ftm: number
  fta: number
  plusMinus: number
}

export interface NBATeamBoxScore {
  players: NBAPlayerBoxScore[]
  totals: {
    points: number
    rebounds: number
    assists: number
    steals: number
    blocks: number
    turnovers: number
    fgPct: number | null
    threePct: number | null
    ftPct: number | null
  }
}

export interface NBAQuarterScore {
  q1: number
  q2: number
  q3: number
  q4: number
  ot?: number[]
  total: number
}

export interface NBAScoringEfficiency {
  ppp: number | null
  tsPct: number | null
  possessions: number | null
}

export interface NBAGameDetail {
  sport: "nba"
  gameId: string
  matchup: NBAMatchupData
  status: GameDetailStatus
  rawStatus: string
  awayScore: NBAQuarterScore
  homeScore: NBAQuarterScore
  awayBoxScore: NBATeamBoxScore | null
  homeBoxScore: NBATeamBoxScore | null
  playByPlay: PlayByPlayEvent[]
  winProbability: WinProbPoint[]
  scoringEfficiency: {
    away: NBAScoringEfficiency
    home: NBAScoringEfficiency
  } | null
}

export interface NHLPlayerSkaterStats {
  name: string
  position: string
  goals: number
  assists: number
  points: number
  plusMinus: number
  pim: number
  shots: number
  hits: number
  blocks: number
  toi: string
}

export interface NHLGoalieStats {
  name: string
  saves: number
  shotsAgainst: number
  savePct: string
  toi: string
}

export interface NHLTeamStats {
  shots: number
  pim: number
  powerPlayGoals: number
  powerPlayOpportunities: number
  faceoffWins: number
  faceoffTotal: number
  hits: number
  blocks: number
  giveaways: number
  takeaways: number
}

export interface NHLTeamBoxScore {
  skaters: NHLPlayerSkaterStats[]
  goalies: NHLGoalieStats[]
  teamStats: NHLTeamStats
}

export interface NHLPeriodScore {
  p1: number
  p2: number
  p3: number
  ot?: number
  so?: number
  total: number
}

export interface NHLThreeStar {
  name: string
  team: string
  position?: string
}

export interface NHLGameDetail {
  sport: "nhl"
  gameId: string
  matchup: NHLMatchupData
  status: GameDetailStatus
  rawStatus: string
  awayScore: NHLPeriodScore
  homeScore: NHLPeriodScore
  awayBoxScore: NHLTeamBoxScore | null
  homeBoxScore: NHLTeamBoxScore | null
  playByPlay: PlayByPlayEvent[]
  winProbability: WinProbPoint[]
  threeStars?: NHLThreeStar[]
}

export type GameDetail = NBAGameDetail | NHLGameDetail
