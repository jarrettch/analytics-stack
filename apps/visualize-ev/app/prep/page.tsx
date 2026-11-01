"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { Clipboard, ClipboardCheck, Plus, X } from "lucide-react";
import { type MatchupData, type Sport, SPORT_LABELS } from "@repo/ui";

const SUPPORTED_SPORTS: Sport[] = ["mlb", "wnba", "nfl", "ncaaf", "nba", "nhl"];

type LoadState =
  | { kind: "idle"; games: PrepGame[] }
  | { kind: "loading"; games: PrepGame[] }
  | { kind: "ready"; games: PrepGame[] }
  | { kind: "error"; games: PrepGame[]; message: string };

type PrepGame = {
  key: string;
  sport: Sport;
  date: string;
  status: string;
  gameStatus: number;
  time: string;
  away: string;
  home: string;
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

type CopyState = "idle" | "copied" | "error";

// Matches the API's slate_date_et(): a slate day runs until 6am ET, so late
// West Coast games stay on the previous day's board instead of vanishing at
// midnight while they're still being played.
const SLATE_ROLLOVER_HOUR = 6;

function easternDateKey(now = new Date()): string {
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

function emptyLoadStates(): Record<Sport, LoadState> {
  return {
    mlb: { kind: "idle", games: [] },
    wnba: { kind: "idle", games: [] },
    nfl: { kind: "idle", games: [] },
    ncaaf: { kind: "idle", games: [] },
    nba: { kind: "idle", games: [] },
    nhl: { kind: "idle", games: [] },
  };
}

function isNfl(
  matchup: MatchupData,
): matchup is Extract<MatchupData, { sport: "nfl" }> {
  return matchup.sport === "nfl";
}

function normalizeGame(matchup: MatchupData): PrepGame {
  return {
    key: `${matchup.sport}:${matchup.gameId}`,
    sport: matchup.sport,
    date: matchup.date.slice(0, 10),
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

function formatGameStatus(game: PrepGame): string {
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

function formatGame(game: PrepGame): string {
  const matchup =
    game.sport === "mlb"
      ? `${game.away} (${pitcherDisplayName(game.awayPitcher)}) vs ${game.home} (${pitcherDisplayName(game.homePitcher)})`
      : `${game.away} @ ${game.home}`;
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

function oddsCoverageWarning(games: PrepGame[]): string | null {
  if (games.length === 0) return null;

  const gamesWithOdds = games.filter(hasMarketOdds).length;
  if (gamesWithOdds === games.length) return null;
  if (gamesWithOdds === 0) {
    return `No market odds were returned for ${games.length} game${games.length === 1 ? "" : "s"}.`;
  }
  return `Market odds were returned for ${gamesWithOdds} of ${games.length} games.`;
}

function groupHeading(sport: Sport, games: PrepGame[]): string {
  if (
    sport === "nfl" &&
    games.length > 0 &&
    games.every((game) => game.isPreseason)
  ) {
    return "NFL — PRESEASON";
  }
  return SPORT_LABELS[sport];
}

function toSlateText(
  sports: Sport[],
  selectedGames: Set<string>,
  states: Record<Sport, LoadState>,
): string {
  return sports
    .flatMap((sport) => {
      const games = states[sport].games.filter((game) =>
        selectedGames.has(game.key),
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

async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(input);
  if (!copied) throw new Error("Browser denied clipboard access");
}

async function fetchSport(sport: Sport): Promise<PrepGame[]> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const endpoint = sport === "nfl" ? "nfl/week" : `${sport}/today`;
  const response = await fetch(`${baseUrl}/api/${endpoint}`);
  if (!response.ok)
    throw new Error(`${SPORT_LABELS[sport]} feed returned ${response.status}`);

  const payload: unknown = await response.json();
  if (!Array.isArray(payload))
    throw new Error(`${SPORT_LABELS[sport]} feed returned an invalid payload`);

  const todayET = easternDateKey();
  return (
    (payload as MatchupData[])
      // Every sport payload normalizes 1 = scheduled, 2 = live, and 3 = final.
      // Keep live games available for an in-progress analysis.
      .filter((matchup) => matchup.gameStatus !== 3)
      .map(normalizeGame)
      .filter((game) => sport === "nfl" || game.date === todayET)
  );
}

export default function SlatePrepPage() {
  const [activeSports, setActiveSports] = useState<Sport[]>([]);
  const activeSportsRef = useRef<Sport[]>([]);
  const [states, setStates] =
    useState<Record<Sport, LoadState>>(emptyLoadStates);
  const [selectedGames, setSelectedGames] = useState<Set<string>>(
    () => new Set(),
  );
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const todayET = easternDateKey();
  const slateText = toSlateText(activeSports, selectedGames, states);
  const selectedCount = activeSports.reduce(
    (total, sport) =>
      total +
      states[sport].games.filter((game) => selectedGames.has(game.key)).length,
    0,
  );

  function setSports(next: Sport[]) {
    activeSportsRef.current = next;
    setActiveSports(next);
  }

  async function addSport(sport: Sport) {
    if (activeSportsRef.current.includes(sport)) return;
    setSports([...activeSportsRef.current, sport]);
    setCopyState("idle");

    const current = states[sport];
    if (current.kind === "ready") {
      setSelectedGames(
        (previous) =>
          new Set([...previous, ...current.games.map((game) => game.key)]),
      );
      return;
    }

    setStates((previous) => ({
      ...previous,
      [sport]: { kind: "loading", games: previous[sport].games },
    }));

    try {
      const games = await fetchSport(sport);
      setStates((previous) => ({
        ...previous,
        [sport]: { kind: "ready", games },
      }));
      if (activeSportsRef.current.includes(sport)) {
        setSelectedGames(
          (previous) =>
            new Set([...previous, ...games.map((game) => game.key)]),
        );
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unable to load games";
      setStates((previous) => ({
        ...previous,
        [sport]: { kind: "error", games: previous[sport].games, message },
      }));
    }
  }

  function removeSport(sport: Sport) {
    setSports(activeSportsRef.current.filter((item) => item !== sport));
    setSelectedGames((previous) => {
      const next = new Set(previous);
      states[sport].games.forEach((game) => next.delete(game.key));
      return next;
    });
    setCopyState("idle");
  }

  function setGameSelected(key: string, selected: boolean) {
    setSelectedGames((previous) => {
      const next = new Set(previous);
      if (selected) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
    setCopyState("idle");
  }

  function setGamesSelected(keys: string[], selected: boolean) {
    setSelectedGames((previous) => {
      const next = new Set(previous);
      keys.forEach((key) => (selected ? next.add(key) : next.delete(key)));
      return next;
    });
    setCopyState("idle");
  }

  function deselectAll() {
    setSelectedGames(new Set());
    setCopyState("idle");
  }

  async function handleCopy() {
    if (!slateText) return;
    try {
      await copyToClipboard(slateText);
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }
  }

  return (
    <main className="min-h-screen bg-background px-4 py-6">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-primary">
              Sports Analytics Terminal
            </p>
            <h1 className="mt-1 text-2xl font-bold text-foreground">
              Slate Prep
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Compose today&apos;s Eastern Time slate or the current NFL week,
              then copy exactly what you selected.
            </p>
          </div>
          <Link
            href="/"
            className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
          >
            ← Dashboard
          </Link>
        </header>

        <section
          aria-labelledby="sports-heading"
          className="rounded-lg border border-border bg-card p-4 shadow-sm"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 id="sports-heading" className="font-semibold text-foreground">
                Add sports
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Daily sports use {todayET} ET. NFL includes the current week.
              </p>
            </div>
            <div className="flex items-center gap-3">
              <span className="font-mono text-[11px] text-muted-foreground">
                {selectedCount} SELECTED
              </span>
              {selectedCount > 0 && (
                <button
                  type="button"
                  onClick={deselectAll}
                  className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline"
                >
                  Deselect all
                </button>
              )}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {SUPPORTED_SPORTS.map((sport) => {
              const state = states[sport];
              const selected = activeSports.includes(sport);
              return (
                <button
                  key={sport}
                  type="button"
                  onClick={() =>
                    selected ? removeSport(sport) : addSport(sport)
                  }
                  disabled={state.kind === "loading"}
                  aria-pressed={selected}
                  className={`inline-flex min-h-9 items-center gap-1.5 rounded-md border px-3 font-mono text-xs uppercase tracking-wider transition-colors disabled:cursor-wait disabled:opacity-60 ${
                    selected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-secondary text-secondary-foreground hover:border-primary/70 hover:text-foreground"
                  }`}
                >
                  {selected ? (
                    <X className="size-3" />
                  ) : (
                    <Plus className="size-3" />
                  )}
                  {state.kind === "loading"
                    ? `Loading ${SPORT_LABELS[sport]}`
                    : SPORT_LABELS[sport]}
                </button>
              );
            })}
          </div>
        </section>

        <section className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.8fr)]">
          <div className="space-y-4">
            {activeSports.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-5 py-10 text-center text-sm text-muted-foreground">
                Add one or more sports to start composing a slate.
              </div>
            ) : (
              activeSports.map((sport) => {
                const state = states[sport];
                const selectedForSport = state.games.filter((game) =>
                  selectedGames.has(game.key),
                );
                const oddsWarning =
                  state.kind === "ready"
                    ? oddsCoverageWarning(state.games)
                    : null;
                const allSelected =
                  state.games.length > 0 &&
                  selectedForSport.length === state.games.length;
                return (
                  <section
                    key={sport}
                    aria-labelledby={`${sport}-heading`}
                    className="rounded-lg border border-border bg-card"
                  >
                    <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
                      <div>
                        <h2
                          id={`${sport}-heading`}
                          className="font-mono text-sm font-semibold tracking-wide text-foreground"
                        >
                          {groupHeading(sport, selectedForSport)}
                        </h2>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {state.kind === "loading"
                            ? `Loading ${sport === "nfl" ? "this week's" : "today's"} games...`
                            : `${selectedForSport.length} game${selectedForSport.length === 1 ? "" : "s"} selected`}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        {state.kind === "ready" && state.games.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              setGamesSelected(
                                state.games.map((game) => game.key),
                                !allSelected,
                              )
                            }
                            className="rounded px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                          >
                            {allSelected ? "Deselect all" : "Select all"}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => removeSport(sport)}
                          className="rounded p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                          aria-label={`Remove ${SPORT_LABELS[sport]} from slate`}
                        >
                          <X className="size-4" />
                        </button>
                      </div>
                    </div>

                    {state.kind === "error" ? (
                      <div className="px-4 py-5 text-sm text-red-300">
                        {state.message}. Other selected sports are still
                        available.
                      </div>
                    ) : state.kind === "loading" ? (
                      <div className="px-4 py-5 font-mono text-xs text-muted-foreground">
                        LOADING DATA FEED…
                      </div>
                    ) : state.games.length === 0 ? (
                      <div className="px-4 py-5 text-sm text-muted-foreground">
                        No scheduled or in-progress {SPORT_LABELS[sport]} games
                        remain {sport === "nfl" ? "this week" : "today"}.
                      </div>
                    ) : (
                      <>
                        {oddsWarning ? (
                          <div className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
                            {oddsWarning} Verify the feed before analysis.
                          </div>
                        ) : null}
                        <ul className="divide-y divide-border">
                          {state.games.map((game) => {
                            const selected = selectedGames.has(game.key);
                            return (
                              <li key={game.key} className="px-4 py-3">
                                <label className="flex cursor-pointer items-start gap-3">
                                  <input
                                    type="checkbox"
                                    checked={selected}
                                    onChange={(event) =>
                                      setGameSelected(
                                        game.key,
                                        event.target.checked,
                                      )
                                    }
                                    className="mt-1 size-4 shrink-0 accent-primary"
                                  />
                                  <span className="min-w-0">
                                    <span
                                      className={`block font-mono text-xs leading-5 ${selected ? "text-foreground" : "text-muted-foreground line-through"}`}
                                    >
                                      {formatGame(game)}
                                    </span>
                                    <span className="mt-1 block text-[11px] text-muted-foreground">
                                      {game.isPreseason ? "PRESEASON · " : ""}
                                      {formatGameStatus(game)}
                                      {!selected ? " · EXCLUDED" : ""}
                                    </span>
                                  </span>
                                </label>
                              </li>
                            );
                          })}
                        </ul>
                      </>
                    )}
                  </section>
                );
              })
            )}
          </div>

          <aside className="h-fit rounded-lg border border-border bg-card p-4 lg:sticky lg:top-4">
            <h2 className="font-semibold text-foreground">Formatted slate</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              This preview is exactly what the copy action exports.
            </p>
            <pre className="mt-4 min-h-64 overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-background p-3 font-mono text-xs leading-5 text-foreground">
              {slateText ||
                "Add sports, then remove any games you do not want to include."}
            </pre>
            <button
              type="button"
              onClick={handleCopy}
              disabled={!slateText}
              className="mt-4 flex min-h-10 w-full items-center justify-center gap-2 rounded-md bg-primary px-4 font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {copyState === "copied" ? (
                <ClipboardCheck className="size-4" />
              ) : (
                <Clipboard className="size-4" />
              )}
              {copyState === "copied"
                ? "Copied formatted slate"
                : "Copy formatted slate"}
            </button>
            {copyState === "error" && (
              <p role="alert" className="mt-2 text-center text-xs text-red-300">
                Clipboard access was unavailable. Select the preview and copy it
                manually.
              </p>
            )}
          </aside>
        </section>
      </div>
    </main>
  );
}
