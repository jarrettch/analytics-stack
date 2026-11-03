"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { Clipboard, ClipboardCheck, Plus, X } from "lucide-react";
import {
  type MatchupData, type Sport, type PrepGame, SPORT_LABELS,
  easternDateKey, normalizeGame, isOnPrepSlate,
  formatGame, formatGameStatus, oddsCoverageWarning, groupHeading, toSlateText,
  createPrepSelection, visiblePrepGames, selectVisiblePrepGames,
  setPrepRankedOnly, prepGroupState,
} from "@repo/ui";

const SUPPORTED_SPORTS = ["mlb", "wnba", "nfl", "ncaaf", "ncaab", "nba", "nhl"] as const satisfies readonly Sport[];
// Prep only tracks its supported sports; dashboard additions do not enable them here.
type PrepSport = (typeof SUPPORTED_SPORTS)[number];

type LoadState =
  | { kind: "idle"; games: PrepGame[] }
  | { kind: "loading"; games: PrepGame[] }
  | { kind: "ready"; games: PrepGame[] }
  | { kind: "error"; games: PrepGame[]; message: string };

type CopyState = "idle" | "copied" | "error";

function emptyLoadStates(): Record<PrepSport, LoadState> {
  return {
    mlb: { kind: "idle", games: [] },
    wnba: { kind: "idle", games: [] },
    nfl: { kind: "idle", games: [] },
    ncaaf: { kind: "idle", games: [] },
    ncaab: { kind: "idle", games: [] },
    nba: { kind: "idle", games: [] },
    nhl: { kind: "idle", games: [] },
  };
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
  return (payload as MatchupData[])
    .map(normalizeGame)
    .filter((game) => isOnPrepSlate(game, todayET));
}

export default function SlatePrepPage() {
  const [activeSports, setActiveSports] = useState<PrepSport[]>([]);
  const activeSportsRef = useRef<PrepSport[]>([]);
  const [states, setStates] =
    useState<Record<PrepSport, LoadState>>(emptyLoadStates);
  const [selection, setSelection] = useState(createPrepSelection);
  const { selectedGames, rankedOnly } = selection;
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const todayET = easternDateKey();
  const slateText = toSlateText(activeSports, selection, states);
  const selectedCount = activeSports.reduce(
    (total, sport) =>
      total +
      visiblePrepGames(states[sport].games, rankedOnly).filter((game) => selectedGames.has(game.key)).length,
    0,
  );

  function setSports(next: PrepSport[]) {
    activeSportsRef.current = next;
    setActiveSports(next);
  }

  async function addSport(sport: PrepSport) {
    if (activeSportsRef.current.includes(sport)) return;
    setSports([...activeSportsRef.current, sport]);
    setCopyState("idle");

    const current = states[sport];
    if (current.kind === "ready") {
      setSelection((previous) => selectVisiblePrepGames(previous, current.games, true));
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
        // Read the latest filter inside the update, even if it changed while loading.
        setSelection((previous) => selectVisiblePrepGames(previous, games, true));
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

  function removeSport(sport: PrepSport) {
    setSports(activeSportsRef.current.filter((item) => item !== sport));
    setSelection((previous) => {
      const next = new Set(previous.selectedGames);
      states[sport].games.forEach((game) => next.delete(game.key));
      return { ...previous, selectedGames: next };
    });
    setCopyState("idle");
  }

  function setGamesSelected(games: PrepGame[], selected: boolean) {
    setSelection((previous) => selectVisiblePrepGames(previous, games, selected));
    setCopyState("idle");
  }

  function setRankedOnly(sport: "ncaab", enabled: boolean) {
    setSelection((previous) => setPrepRankedOnly(previous, sport, enabled, states[sport].games));
    setCopyState("idle");
  }

  function deselectAll() {
    setGamesSelected(activeSports.flatMap((sport) => states[sport].games), false);
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
                const {
                  games: visibleGames, selected: selectedForSport, allSelected, emptyMessage,
                } = prepGroupState(sport, state.games, selection);
                const oddsWarning =
                  state.kind === "ready"
                    ? oddsCoverageWarning(visibleGames)
                    : null;
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
                        {sport === "ncaab" && (
                          <button
                            type="button"
                            aria-pressed={rankedOnly[sport] ?? false}
                            onClick={() => setRankedOnly(sport, !rankedOnly[sport])}
                            className={`rounded px-2 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors ${rankedOnly[sport] ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary hover:text-foreground"}`}
                          >
                            Ranked only
                          </button>
                        )}
                        {state.kind === "ready" && visibleGames.length > 0 && (
                          <button
                            type="button"
                            onClick={() =>
                              setGamesSelected(
                                state.games,
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
                    ) : visibleGames.length === 0 ? (
                      <div className="px-4 py-5 text-sm text-muted-foreground">
                        {emptyMessage}
                      </div>
                    ) : (
                      <>
                        {oddsWarning ? (
                          <div className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
                            {oddsWarning} Verify the feed before analysis.
                          </div>
                        ) : null}
                        <ul className="divide-y divide-border">
                          {visibleGames.map((game) => {
                            const selected = selectedGames.has(game.key);
                            return (
                              <li key={game.key} className="px-4 py-3">
                                <label className="flex cursor-pointer items-start gap-3">
                                  <input
                                    type="checkbox"
                                    checked={selected}
                                    onChange={(event) =>
                                      setGamesSelected(
                                        [game],
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
