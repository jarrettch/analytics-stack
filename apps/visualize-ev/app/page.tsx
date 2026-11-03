"use client";

import { useEffect, useMemo, useState, useRef } from "react";
import Link from "next/link";
import {
  SportsDashboard,
  type Sport,
  type MatchupData,
  SPORT_LABELS,
  SPORT_MODELS,
  collegeFilterForSlate,
  conferenceOptions,
  createCollegeFilters,
  passesCollegeFilters,
  updateCollegeFilters,
} from "@repo/ui";
import { Activity, ClipboardList, Crosshair, Swords } from "lucide-react";

const FALLBACK_VISIBLE_SPORTS: Sport[] = ["mlb", "wnba"];

// Sports with a live backend endpoint (others fall through to "coming soon").
const LIVE_SPORTS: Sport[] = ["nba", "wnba", "nfl", "mlb", "nhl", "ncaaf", "ncaab"];
const LAST_SELECTED_SPORT_KEY = "sports-dashboard:last-selected-sport";

function storedSport(): Sport | null {
  try {
    const value = window.localStorage.getItem(LAST_SELECTED_SPORT_KEY);
    return LIVE_SPORTS.includes(value as Sport) ? value as Sport : null;
  } catch {
    return null;
  }
}

function rememberSport(sport: Sport) {
  try {
    window.localStorage.setItem(LAST_SELECTED_SPORT_KEY, sport);
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
}

function hasGameStatus(matchup: MatchupData): matchup is MatchupData & { gameStatus: number } {
  return "gameStatus" in matchup && typeof matchup.gameStatus === "number";
}

async function fetchSportSlate(sport: Sport): Promise<MatchupData[]> {
  const endpoint = sport === "nfl" ? "nfl/week" : `${sport}/today`;
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/${endpoint}`);
  if (!res.ok) {
    throw new Error(`${sport.toUpperCase()} feed returned ${res.status}`);
  }

  const data: unknown = await res.json();
  if (!Array.isArray(data)) {
    throw new Error(`${sport.toUpperCase()} feed returned a non-array payload`);
  }

  return data as MatchupData[];
}

export default function Home() {
  const [activeSport, setActiveSport] = useState<Sport>("mlb");
  const [visibleSports, setVisibleSports] = useState<Sport[] | null>(null);
  const [matchups, setMatchups] = useState<MatchupData[]>([]);
  const [loading, setLoading] = useState(true);
  const [preferenceReady, setPreferenceReady] = useState(false);
  const [now, setNow] = useState<Date | null>(null);
  const [collegeFilters, setCollegeFilters] = useState(createCollegeFilters);
  const lastDataRef = useRef<string>("");
  const preferredSportRef = useRef<Sport | null>(null);
  const userSelectedSportRef = useRef(false);

  useEffect(() => {
    setNow(new Date());
  }, []);

  useEffect(() => {
    const preferred = storedSport();
    preferredSportRef.current = preferred;
    if (preferred) setActiveSport(preferred);
    setPreferenceReady(true);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function selectInitialSport() {
      try {
        const settled = await Promise.allSettled(
          LIVE_SPORTS.map(async (sport) => {
            const data = await fetchSportSlate(sport);
            return { sport, data };
          }),
        );
        const results = settled.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);

        if (cancelled || userSelectedSportRef.current) return;

        const live = results.find(({ data }) => data.some(m => hasGameStatus(m) && m.gameStatus === 2));
        const populated = results.find(({ data }) => data.length > 0);
        const preferred = results.find(
          ({ sport, data }) => sport === preferredSportRef.current && data.length > 0,
        );
        const selected = preferred ?? live ?? populated;
        const sportsWithSlates = results
          .filter(({ data }) => data.length > 0)
          .map(({ sport }) => sport);

        setVisibleSports(sportsWithSlates.length > 0 ? sportsWithSlates : FALLBACK_VISIBLE_SPORTS);

        if (selected) {
          setActiveSport(selected.sport);
          setMatchups(selected.data);
          lastDataRef.current = JSON.stringify(selected.data);
          setLoading(false);
        }
      } catch (err) {
        console.error("Failed to auto-select sport:", err);
        if (!cancelled) setVisibleSports(FALLBACK_VISIBLE_SPORTS);
      }
    }

    selectInitialSport();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!preferenceReady) return;

    const fetchData = async (isInitial = false) => {
      if (LIVE_SPORTS.includes(activeSport)) {
        if (isInitial) setLoading(true);

        const endpoint = activeSport === "nfl" ? "nfl/week" : `${activeSport}/today`;
        try {
          const data = await fetchSportSlate(activeSport);

          // Only update if data actually changed (prevents unnecessary re-renders)
          const dataStr = JSON.stringify(data);

          if (dataStr !== lastDataRef.current) {
            console.log("Data updated");
            lastDataRef.current = dataStr;
            setMatchups(data);
          }

          setLoading(false);
        } catch (err) {
          console.error(`Failed to fetch ${endpoint}:`, err);
          setLoading(false);
        }
      } else {
        setMatchups([]);
        setLoading(false);
      }
    };

    // Initial fetch
    fetchData(true);

    // Set up polling for live games
    let pollInterval: NodeJS.Timeout | null = null;

    if (LIVE_SPORTS.includes(activeSport)) {
      pollInterval = setInterval(() => fetchData(false), 15000);
    }

    // Cleanup
    return () => {
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [activeSport, preferenceReady]);

  const collegeSport = activeSport === "ncaaf" || activeSport === "ncaab" ? activeSport : null;
  const conferences = useMemo(
    () => collegeSport ? conferenceOptions(matchups, collegeSport) : [],
    [matchups, collegeSport],
  );
  const activeCollegeFilter = useMemo(
    () => collegeSport ? collegeFilterForSlate(collegeFilters, collegeSport, conferences) : null,
    [collegeFilters, collegeSport, conferences],
  );
  const displayedMatchups = useMemo(
    () => activeCollegeFilter
      ? matchups.filter((matchup) => passesCollegeFilters(matchup, activeCollegeFilter))
      : matchups,
    [matchups, activeCollegeFilter],
  );
  const gameCount = displayedMatchups.length;
  // Reads the filtered view, like the game count beside it: LIVE means a live
  // game is on screen, not merely somewhere on the unfiltered slate.
  const hasLiveGames = displayedMatchups.some(m => hasGameStatus(m) && m.gameStatus === 2);

  return (
    <main className="min-h-screen bg-background px-4 py-6">
      {/* Dashboard header */}
      <div className="mx-auto mb-4 max-w-6xl">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex items-center gap-3">
            <Activity className="size-5 text-primary" />
            <h1 className="font-sans text-lg font-bold text-foreground">
              Sports Analytics Terminal
            </h1>
            {hasLiveGames && (
              <span className="font-mono text-[10px] uppercase tracking-widest text-emerald-400 animate-pulse rounded-sm bg-emerald-500/10 px-0.5 py-0.5 [filter:drop-shadow(0_0_6px_#34d399)]">
                Live
              </span>
            )}
          </div>
          <div className="flex items-center gap-4">
            <Link
              href="/prep"
              className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
            >
              <ClipboardList className="size-3" />
              Slate Prep
            </Link>
            <Link
              href="/mlb/pitcher-strikeout-odds"
              className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
            >
              <Crosshair className="size-3" />
              Oh-K Corral
            </Link>
            <Link
              href="/ufc"
              className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
            >
              <Swords className="size-3" />
              UFC
            </Link>
            <span className="hidden min-w-36 text-right font-mono text-[11px] tabular-nums text-muted-foreground sm:inline">
              {now
                ? `${now.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase()} — MULTI-SPORT`
                : "\u00A0"}
            </span>
          </div>
        </div>
        <div className="mt-2 h-px w-full bg-border" />
      </div>

      {/* Sport tabs */}
      <div className="mx-auto max-w-6xl">
        <div className="w-full">
          <div className="flex items-center justify-between">
            {/* Tabs */}
            {/* Scrolls rather than wraps or collapsing into a dropdown: the tabs
                show which sports have games. Revisit at seven tabs. */}
            <div className="flex h-8 min-w-52 max-w-full overflow-x-auto whitespace-nowrap bg-secondary rounded-md">
              {(visibleSports ?? []).map((sport) => (
                <button
                  key={sport}
                  onClick={() => {
                    userSelectedSportRef.current = true;
                    preferredSportRef.current = sport;
                    rememberSport(sport);
                    setActiveSport(sport);
                  }}
                  className={`shrink-0 px-4 font-mono text-xs uppercase tracking-wider transition-colors ${
                    activeSport === sport
                      ? "bg-primary text-primary-foreground rounded-md"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {SPORT_LABELS[sport]}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-4">
              <span className="font-mono text-[10px] text-muted-foreground">
                MODEL: {SPORT_MODELS[activeSport]}
              </span>
              <span className="font-mono text-[10px] text-muted-foreground">
                {gameCount} GAME{gameCount !== 1 ? "S" : ""}
              </span>
            </div>
          </div>

          <div className="mt-4 h-px w-full bg-border" />

          {collegeSport && activeCollegeFilter && matchups.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-3 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              <label className="flex items-center gap-1.5">
                Conf
                <select
                  value={activeCollegeFilter.conferenceId}
                  onChange={(event) => setCollegeFilters((previous) =>
                    updateCollegeFilters(previous, collegeSport, { conferenceId: event.target.value }),
                  )}
                  className="rounded-sm border border-border bg-secondary px-1.5 py-0.5 text-foreground"
                >
                  <option value="">All</option>
                  {conferences.map(([id, label]) => (
                    <option key={id} value={id}>{label}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={activeCollegeFilter.rankedOnly}
                  onChange={(event) => setCollegeFilters((previous) =>
                    updateCollegeFilters(previous, collegeSport, { rankedOnly: event.target.checked }),
                  )}
                />
                Ranked involved
              </label>
            </div>
          )}

          {/* Content */}
          {/* Reserve the grid's rough height so the footer doesn't jump when
              the spinner is replaced by cards. */}
          <div className="mt-4 min-h-[70vh]">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-20 font-mono text-xs text-muted-foreground">
                <div className="mb-2 h-4 w-4 animate-spin border-2 border-primary border-t-transparent rounded-full" />
                INITIALIZING DATA FEED...
              </div>
            ) : displayedMatchups.length > 0 ? (
              <SportsDashboard
                matchups={displayedMatchups}
                apiBaseUrl={process.env.NEXT_PUBLIC_API_URL}
              />
            ) : (
              <div className="flex flex-col items-center justify-center py-20 font-mono text-xs text-muted-foreground">
                <span className="uppercase">
                  {matchups.length > 0
                    ? `No ${SPORT_LABELS[activeSport]} games match these filters`
                    : `No ${SPORT_LABELS[activeSport]} games scheduled ${activeSport === "nfl" ? "this week" : "today"}`}
                </span>
                {!LIVE_SPORTS.includes(activeSport) && (
                  <span className="mt-2 text-[10px] text-muted-foreground/60">
                    ({SPORT_LABELS[activeSport]} data coming soon)
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      {/* Held back until the slate resolves. Any reserved height is a guess at
          the grid's final size, and the guess fails hardest on mobile, where a
          single-column grid runs far past it and shoves the footer thousands of
          pixels down. An element that mounts below the fold shifts nothing, so
          rendering it late costs no layout and no CLS. */}
      {!loading && (
      <div className="mx-auto mt-6 max-w-6xl">
        <div className="h-px w-full bg-border" />
        <div className="flex items-center justify-between pt-2">
          <span className="font-mono text-[10px] text-muted-foreground">
            Models: EV-MODEL-V1 (NBA/WNBA) &bull; Market + team stats (NFL) &bull; Market, DraftKings only (NCAAF/NCAAB) &bull; PECOTA v2.8 (MLB) &bull; xG v5.0 (NHL)
          </span>
          <span className="min-w-64 text-right font-mono text-[10px] tabular-nums text-muted-foreground">
            {now
              ? `Updated ${now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ET • For entertainment only`
              : "\u00A0"}
          </span>
        </div>
      </div>
      )}
    </main>
  );
}
