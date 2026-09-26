// UFC deliberately shares no type with MatchupData: a fight has no home side,
// no spread, and no points total. See apps/api/specs/ufc_page.md.

export interface UFCFighterData {
  name: string
  espnAthleteId: string
  moneyline: number | null
  winProbability: number | null   // de-vigged from the moneyline pair
  winner: boolean | null          // null until the fight is final
}

// A market retained after the book stopped offering it: last observed, not current.
export interface UFCMarketFreshness {
  isStale: boolean
  lastObservedAt: string | null
}

export interface UFCFightData {
  fightId: string                 // ESPN competition id
  order: number                   // 1 = main event; the card renders in this order
  weightClass: string             // competition.type.abbreviation, e.g. "W Strawweight"
  status: string                  // display text, e.g. "10:15 PM ET", "Final"
  gameStatus: number              // 1 scheduled, 2 live, 3 final — matches every other sport
  bookmaker: string | null        // the one book pricing everything shown; null when unpriced
  fighters: [UFCFighterData, UFCFighterData]
  totalRounds: ({
    line: number
    overOdds: number | null
    underOdds: number | null
  } & UFCMarketFreshness) | null
  goesTheDistance: ({
    yesOdds: number | null
    noOdds: number | null
  } & UFCMarketFreshness) | null
}

export interface UFCCardData {
  cardId: string                  // ESPN event id
  name: string                    // "Noche UFC: Silva vs. Delgado"
  date: string
  venue: string
  broadcast: string
  fights: UFCFightData[]
}
