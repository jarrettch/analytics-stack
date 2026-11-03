import type { MatchupData } from "../types/sports-types"

export type CollegeSport = "ncaaf" | "ncaab"
type CollegeMatchup = Extract<MatchupData, { sport: CollegeSport }>
type CollegeFilter = { conferenceId: string; rankedOnly: boolean }
export type CollegeFilters = Record<CollegeSport, CollegeFilter>

export function createCollegeFilters(): CollegeFilters {
  return {
    ncaaf: { conferenceId: "", rankedOnly: false },
    ncaab: { conferenceId: "", rankedOnly: true },
  }
}

export function updateCollegeFilters(
  filters: CollegeFilters,
  sport: CollegeSport,
  update: Partial<CollegeFilter>,
): CollegeFilters {
  return { ...filters, [sport]: { ...filters[sport], ...update } }
}

export function isCollege(matchup: MatchupData): matchup is CollegeMatchup {
  return matchup.sport === "ncaaf" || matchup.sport === "ncaab"
}

export function conferenceOptions(matchups: MatchupData[], sport: CollegeSport): [string, string][] {
  const options = new Map<string, string>()
  for (const matchup of matchups.filter(isCollege)) {
    if (matchup.sport !== sport) continue
    for (const team of [matchup.away, matchup.home]) {
      if (team.conferenceId && team.conference !== "N/A") {
        options.set(team.conferenceId, team.conference)
      }
    }
  }
  return [...options].sort((a, b) => a[1].localeCompare(b[1]))
}

// Apply only a conference the current select can show, without discarding the
// saved preference: it can become available again on a later slate.
export function collegeFilterForSlate(
  filters: CollegeFilters,
  sport: CollegeSport,
  conferences: [string, string][],
): CollegeFilter {
  const filter = filters[sport]
  return {
    ...filter,
    conferenceId: conferences.some(([id]) => id === filter.conferenceId) ? filter.conferenceId : "",
  }
}

export function passesCollegeFilters(matchup: MatchupData, filter: CollegeFilter): boolean {
  if (!isCollege(matchup)) return true
  const teams = [matchup.away, matchup.home]
  if (filter.rankedOnly && !teams.some((team) => team.rank != null)) return false
  return !filter.conferenceId || teams.some((team) => team.conferenceId === filter.conferenceId)
}
