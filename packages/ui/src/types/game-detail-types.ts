import type { MLBMatchupData, NBAMatchupData, NFLMatchupData, NHLMatchupData } from "./sports-types"

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
  /** Why the player sat, e.g. "Coach's Decision" or "Right Calf, Soreness"; absent if they played or may still. */
  dnp?: string | null
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

export interface NFLPlayerPassingStats {
  name: string
  completions: number
  attempts: number
  yards: number
  touchdowns: number
  interceptions: number
  rating: number
  sacks: number
}

export interface NFLPlayerRushingStats {
  name: string
  carries: number
  yards: number
  average: number
  touchdowns: number
  longest: number
}

export interface NFLPlayerReceivingStats {
  name: string
  receptions: number
  targets: number
  yards: number
  average: number
  touchdowns: number
  longest: number
}

export interface NFLTeamBoxScore {
  passing: NFLPlayerPassingStats[]
  rushing: NFLPlayerRushingStats[]
  receiving: NFLPlayerReceivingStats[]
  teamStats: {
    firstDowns: number
    totalYards: number
    passingYards: number
    rushingYards: number
    penalties: number
    penaltyYards: number
    turnovers: number
    possession: string
    thirdDownEff: string
    fourthDownEff: string
    redZoneEff: string
  }
}

export interface NFLQuarterScore {
  q1: number
  q2: number
  q3: number
  q4: number
  ot?: number[]
  total: number
}

export interface NFLGameDetail {
  sport: "nfl"
  gameId: string
  matchup: NFLMatchupData
  status: GameDetailStatus
  rawStatus: string
  awayScore: NFLQuarterScore
  homeScore: NFLQuarterScore
  awayBoxScore: NFLTeamBoxScore | null
  homeBoxScore: NFLTeamBoxScore | null
  playByPlay: PlayByPlayEvent[]
  winProbability: WinProbPoint[]
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

export interface MLBPlayerHittingStats {
  name: string
  position: string
  ab: number
  runs: number
  hits: number
  rbi: number
  hr: number
  bb: number
  so: number
  avg: string
  obp: string
  slg: string
}

export interface MLBPlayerPitchingStats {
  name: string
  ip: string
  hits: number
  runs: number
  earnedRuns: number
  bb: number
  so: number
  hr: number
  pitchCount: number
  strikes: number
  era: string
}

export interface MLBTeamBoxScore {
  hitting: MLBPlayerHittingStats[]
  pitching: MLBPlayerPitchingStats[]
  teamBatting: {
    ab: number
    runs: number
    hits: number
    rbi: number
    hr: number
    bb: number
    so: number
    lob: number
    risp: string
  }
}

export interface MLBInningScore {
  innings: (number | null)[]
  runs: number
  hits: number
  errors: number
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

export interface MLBGameDetail {
  sport: "mlb"
  gameId: string
  matchup: MLBMatchupData
  status: GameDetailStatus
  rawStatus: string
  awayScore: MLBInningScore
  homeScore: MLBInningScore
  awayBoxScore: MLBTeamBoxScore | null
  homeBoxScore: MLBTeamBoxScore | null
  playByPlay: PlayByPlayEvent[]
  winProbability: WinProbPoint[]
}

export type GameDetail = NBAGameDetail | NFLGameDetail | NHLGameDetail | MLBGameDetail
