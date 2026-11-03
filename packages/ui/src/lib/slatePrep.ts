import { type MatchupData, type Sport, SPORT_LABELS } from "../types/sports-types";

export type PrepGame = {
  key: string;
  sport: Sport;
  date: string;
  slateDate?: string;
  status: string;
  gameStatus: number;
  time: string;
  away: string;
  home: string;
  awayRank?: number | null;
  homeRank?: number | null;
  awayPitcher?: string;
  homePitcher?: string;
  awayMoneyline?: number | null;
  homeMoneyline?: number | null;
  homeSpread?: number | null;
  spreadOddsAway?: number | null;
  spreadOddsHome?: number | null;
  overUnder?: number | null;
  overOdds?: number | null;
  underOdds?: number | null;
  isPreseason: boolean;
};

// Matches the API's slate_date_et(): a slate day runs until 6am ET, so late
// West Coast games stay on the previous day's board instead of vanishing at
// midnight while they're still being played.
const SLATE_ROLLOVER_HOUR = 6;

export function easternDateKey(now = new Date()): string {
  const rolled = new Date(now.getTime() - SLATE_ROLLOVER_HOUR * 60 * 60 * 1000);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(rolled);
  const value = Object.fromEntries(
    parts.map(({ type, value }) => [type, value]),
  );
  return `${value.year}-${value.month}-${value.day}`;
}

export function isOnPrepSlate(game: PrepGame, slateDate: string): boolean {
  // Keep live games; NFL intentionally spans the whole current week.
  return game.gameStatus !== 3 &&
    (game.sport === "nfl" || (game.slateDate ?? game.date) === slateDate);
}

export type PrepSelection = {
  selectedGames: Set<string>;
  rankedOnly: Partial<Record<Sport, boolean>>;
};

export function createPrepSelection(): PrepSelection {
  return { selectedGames: new Set(), rankedOnly: { ncaab: true } };
}

export function visiblePrepGames(
  games: PrepGame[],
  rankedOnly: PrepSelection["rankedOnly"],
): PrepGame[] {
  return games.filter((game) => game.sport !== "ncaab" || (
    hasMarketOdds(game) &&
    (!rankedOnly.ncaab || game.awayRank != null || game.homeRank != null)
  ));
}

export function selectVisiblePrepGames(
  selection: PrepSelection,
  games: PrepGame[],
  selected: boolean,
): PrepSelection {
  const next = new Set(selection.selectedGames);
  for (const game of visiblePrepGames(games, selection.rankedOnly)) {
    if (selected) next.add(game.key);
    else next.delete(game.key);
  }
  return { ...selection, selectedGames: next };
}

export function setPrepRankedOnly(
  selection: PrepSelection,
  sport: "ncaab",
  enabled: boolean,
  games: PrepGame[],
): PrepSelection {
  const rankedOnly = { ...selection.rankedOnly, [sport]: enabled };
  const selectedGames = new Set(selection.selectedGames);
  const visible = new Set(visiblePrepGames(games, rankedOnly).map((game) => game.key));
  for (const game of games) {
    if (game.sport === sport && !visible.has(game.key)) selectedGames.delete(game.key);
  }
  // Revealing games never selects them.
  return { rankedOnly, selectedGames };
}

export function prepGroupState(sport: Sport, games: PrepGame[], selection: PrepSelection) {
  const visible = visiblePrepGames(games, selection.rankedOnly);
  const selected = visible.filter((game) => selection.selectedGames.has(game.key));
  return {
    games: visible,
    selected,
    allSelected: visible.length > 0 && selected.length === visible.length,
    emptyMessage: visible.length > 0 ? null
      : sport === "ncaab" && selection.rankedOnly.ncaab ? "No ranked teams today"
      : `No scheduled or in-progress ${SPORT_LABELS[sport]} games remain ${sport === "nfl" ? "this week" : "today"}.`,
  };
}

function isNfl(
  matchup: MatchupData,
): matchup is Extract<MatchupData, { sport: "nfl" }> {
  return matchup.sport === "nfl";
}

export function normalizeGame(matchup: MatchupData): PrepGame {
  return {
    key: `${matchup.sport}:${matchup.gameId}`,
    sport: matchup.sport,
    date: matchup.date.slice(0, 10),
    slateDate: "slateDate" in matchup ? matchup.slateDate : undefined,
    awayRank: "rank" in matchup.away ? matchup.away.rank : undefined,
    homeRank: "rank" in matchup.home ? matchup.home.rank : undefined,
    status:
      matchup.sport === "nba" && matchup.gameStatus === 2
        ? formatNbaLiveStatus(matchup)
        : matchup.status,
    gameStatus: matchup.gameStatus,
    time: matchup.time,
    away: matchup.away.name || matchup.away.abbreviation,
    home: matchup.home.name || matchup.home.abbreviation,
    awayPitcher:
      matchup.sport === "mlb" ? matchup.away.probablePitcher : undefined,
    homePitcher:
      matchup.sport === "mlb" ? matchup.home.probablePitcher : undefined,
    awayMoneyline: matchup.awayMoneyline,
    homeMoneyline: matchup.homeMoneyline,
    homeSpread: matchup.pregameSpread,
    spreadOddsAway: matchup.spreadOddsAway,
    spreadOddsHome: matchup.spreadOddsHome,
    overUnder: matchup.overUnder,
    overOdds: matchup.overOdds,
    underOdds: matchup.underOdds,
    isPreseason:
      isNfl(matchup) && matchup.seasonType.toLowerCase().includes("preseason"),
  };
}

function pitcherDisplayName(name?: string): string {
  if (!name || name.trim().toUpperCase() === "TBD") return "TBD";
  return name.trim();
}

function formatNbaLiveStatus(
  matchup: Extract<MatchupData, { sport: "nba" }>,
): string {
  // Preserve breaks rather than showing a stopped clock as ongoing play.
  if (/half|end|break/i.test(matchup.status)) return matchup.status;
  if (!matchup.period) return matchup.status || "Live";
  const period =
    matchup.period <= 4
      ? `Q${matchup.period}`
      : matchup.period === 5
        ? "OT"
        : `${matchup.period - 4}OT`;
  const iso = /^PT(\d+)M([\d.]+)S$/.exec(matchup.gameClock || "");
  const clock = iso
    ? `${Number(iso[1])}:${Math.floor(Number(iso[2])).toString().padStart(2, "0")}`
    : matchup.gameClock || "";
  return `${period} ${clock}`.trim();
}

export function formatGameStatus(game: PrepGame): string {
  const status = game.status || game.time || "Scheduled";
  if (game.sport !== "nba") return status;
  if (game.gameStatus !== 1) return status;

  // The NBA feed supplies a 24-hour Eastern tip time separately from status.
  const clock = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(game.time);
  if (!clock) return status;
  const hour = Number(clock[1]);
  const tip = `${hour % 12 || 12}:${clock[2]} ${hour >= 12 ? "PM" : "AM"} ET`;
  return game.status && game.status !== game.time
    ? `${tip} · ${game.status}`
    : tip;
}

function formatAmericanOdds(value?: number | null): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return value > 0 ? `+${value}` : `${value}`;
}

function formatSpread(value: number): string {
  return value > 0 ? `+${value}` : `${value}`;
}

function formatLine(game: PrepGame): string {
  const awayMoneyline = formatAmericanOdds(game.awayMoneyline);
  const homeMoneyline = formatAmericanOdds(game.homeMoneyline);
  const parts: string[] = [];

  if (typeof game.homeSpread === "number" && Number.isFinite(game.homeSpread)) {
    const awaySpread = formatSpread(-game.homeSpread);
    const homeSpread = formatSpread(game.homeSpread);
    const awayPrice = formatAmericanOdds(game.spreadOddsAway);
    const homePrice = formatAmericanOdds(game.spreadOddsHome);
    const prices = awayPrice && homePrice ? ` (${awayPrice}/${homePrice})` : "";
    parts.push(
      `${game.sport === "mlb" ? "RL" : "Spread"} ${awaySpread}/${homeSpread}${prices}`,
    );
  }
  if (awayMoneyline && homeMoneyline) {
    parts.push(`ML ${awayMoneyline}/${homeMoneyline}`);
  }
  if (typeof game.overUnder === "number" && Number.isFinite(game.overUnder)) {
    // Prices are labelled o/u because, unlike the ML pair, their order isn't
    // implied by anything else on the line.
    const over = formatAmericanOdds(game.overOdds);
    const under = formatAmericanOdds(game.underOdds);
    const prices = over && under ? ` (o${over}/u${under})` : "";
    parts.push(`O/U ${game.overUnder}${prices}`);
  }

  return parts.length ? ` — ${parts.join(", ")}` : "";
}

function rankedName(game: PrepGame, name: string, rank?: number | null): string {
  return game.sport === "ncaab" && rank != null ? `#${rank} ${name}` : name;
}

export function formatGame(game: PrepGame): string {
  const matchup =
    game.sport === "mlb"
      ? `${game.away} (${pitcherDisplayName(game.awayPitcher)}) vs ${game.home} (${pitcherDisplayName(game.homePitcher)})`
      : `${rankedName(game, game.away, game.awayRank)} @ ${rankedName(game, game.home, game.homeRank)}`;
  return `${matchup}${formatLine(game)}`;
}

function hasMarketOdds(game: PrepGame): boolean {
  return [
    game.awayMoneyline,
    game.homeMoneyline,
    game.homeSpread,
    game.spreadOddsAway,
    game.spreadOddsHome,
    game.overUnder,
    game.overOdds,
    game.underOdds,
  ].some((value) => typeof value === "number" && Number.isFinite(value));
}

export function oddsCoverageWarning(games: PrepGame[]): string | null {
  if (games.length === 0) return null;

  const gamesWithOdds = games.filter(hasMarketOdds).length;
  if (gamesWithOdds === games.length) return null;
  if (gamesWithOdds === 0) {
    return `No market odds were returned for ${games.length} game${games.length === 1 ? "" : "s"}.`;
  }
  return `Market odds were returned for ${gamesWithOdds} of ${games.length} games.`;
}

export function groupHeading(sport: Sport, games: PrepGame[]): string {
  if (
    sport === "nfl" &&
    games.length > 0 &&
    games.every((game) => game.isPreseason)
  ) {
    return "NFL — PRESEASON";
  }
  return SPORT_LABELS[sport];
}

export function toSlateText(
  sports: readonly Sport[],
  selection: PrepSelection,
  states: Partial<Record<Sport, { games: PrepGame[] }>>,
): string {
  return sports
    .flatMap((sport) => {
      const games = visiblePrepGames(states[sport]?.games ?? [], selection.rankedOnly).filter((game) =>
        selection.selectedGames.has(game.key),
      );
      if (!games.length) return [];

      const oddsWarning = oddsCoverageWarning(games);
      return [
        [
          groupHeading(sport, games),
          oddsWarning ? `ODDS WARNING: ${oddsWarning}` : null,
          games.map(formatGame).join("\n"),
        ]
          .filter(Boolean)
          .join("\n"),
      ];
    })
    .join("\n\n");
}

