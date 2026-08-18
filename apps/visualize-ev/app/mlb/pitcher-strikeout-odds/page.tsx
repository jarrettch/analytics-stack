"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  Crosshair,
  Layers,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";

type OddsPair = {
  line: number;
  overOdds: number | null;
  underOdds: number | null;
  capturedAt: string;
  marketPhase: string;
};

type AltRung = { line: number; overOdds: number | null; underOdds: number | null };

type BookView = {
  bookmakerId: string;
  current: OddsPair;
  first: OddsPair;
  open: { line: number; overOdds: number | null; underOdds: number | null } | null;
  alternates: AltRung[];
};

type PitcherEntry = {
  eventId: string;
  playerId: string;
  playerName: string;
  playerTeam: string;
  opponentTeam: string;
  homeTeam: string;
  awayTeam: string;
  startsAt: string | null;
  marketPhase: string;
  lastCapturedAt: string;
  books: BookView[];
};

type BoardResponse = {
  date: string;
  generatedAt: string;
  lastObservedAt: string | null;
  stale: boolean;
  pitchers: PitcherEntry[];
};

type HistoryBook = { bookmakerId: string; points: OddsPair[] };
type HistoryResponse = { date: string; playerId: string; playerName: string; books: HistoryBook[] };

// Fixed identity colors per bookmaker (validated 6-slot dark categorical set);
// a book keeps its color no matter which columns are on screen.
const BOOK_META: Record<string, { label: string; color: string }> = {
  fanduel: { label: "FD", color: "#3987e5" },
  draftkings: { label: "DK", color: "#d95926" },
  betmgm: { label: "MGM", color: "#199e70" },
  caesars: { label: "CZR", color: "#c98500" },
  espnbet: { label: "ESPN", color: "#d55181" },
  bovada: { label: "BOV", color: "#008300" },
};
const BOOK_ORDER = Object.keys(BOOK_META);
const SURFACE = "hsl(222 84% 4.9%)";

function bookMeta(id: string) {
  return BOOK_META[id] ?? { label: id.slice(0, 4).toUpperCase(), color: "#7f8ea3" };
}

function bookSortKey(id: string): [number, string] {
  const index = BOOK_ORDER.indexOf(id);
  return [index === -1 ? BOOK_ORDER.length : index, id];
}

function fmtOdds(odds: number | null): string {
  if (odds === null) return "—";
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function fmtTimeET(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
  });
}

/** Most common line across books; ties break toward the lower line. */
function consensusLine(books: BookView[], key: "current" | "first"): number | null {
  const counts = new Map<number, number>();
  for (const book of books) counts.set(book[key].line, (counts.get(book[key].line) ?? 0) + 1);
  let best: number | null = null;
  for (const [line, count] of counts) {
    if (best === null || count > (counts.get(best) ?? 0) || (count === counts.get(best) && line < best)) {
      best = line;
    }
  }
  return best;
}

/** Best bettor price per side among books currently at the consensus line. */
function bestPrices(books: BookView[], consensus: number | null) {
  let over: { odds: number; bookmakerId: string } | null = null;
  let under: { odds: number; bookmakerId: string } | null = null;
  for (const book of books) {
    if (book.current.line !== consensus) continue;
    const { overOdds, underOdds } = book.current;
    if (overOdds !== null && (over === null || overOdds > over.odds)) {
      over = { odds: overOdds, bookmakerId: book.bookmakerId };
    }
    if (underOdds !== null && (under === null || underOdds > under.odds)) {
      under = { odds: underOdds, bookmakerId: book.bookmakerId };
    }
  }
  return { over, under };
}

function PhaseBadge({ phase }: { phase: string }) {
  if (phase === "live") {
    return (
      <span className="animate-pulse rounded-sm bg-emerald-500/10 px-1 py-0.5 font-mono text-[9px] uppercase tracking-widest text-emerald-400">
        Live
      </span>
    );
  }
  if (phase === "final" || phase === "cancelled") {
    return (
      <span className="rounded-sm bg-secondary px-1 py-0.5 font-mono text-[9px] uppercase tracking-widest text-muted-foreground">
        {phase}
      </span>
    );
  }
  return null;
}

function HistoryChart({ books }: { books: HistoryBook[] }) {
  const width = 640;
  const height = 210;
  const pad = { left: 34, right: 14, top: 14, bottom: 30 };

  const times = useMemo(
    () =>
      [...new Set(books.flatMap((book) => book.points.map((p) => p.capturedAt)))].sort(),
    [books],
  );
  const lines = books.flatMap((book) => book.points.map((p) => p.line));
  if (times.length === 0 || lines.length === 0) return null;

  const t0 = new Date(times[0]!).getTime();
  const t1 = new Date(times[times.length - 1]!).getTime();
  const yMin = Math.min(...lines) - 0.5;
  const yMax = Math.max(...lines) + 0.5;
  const x = (iso: string) => {
    if (t1 === t0) return pad.left + (width - pad.left - pad.right) / 2;
    const t = new Date(iso).getTime();
    return pad.left + ((t - t0) / (t1 - t0)) * (width - pad.left - pad.right);
  };
  const y = (line: number) =>
    pad.top + ((yMax - line) / (yMax - yMin)) * (height - pad.top - pad.bottom);

  const tickStep = yMax - yMin > 4 ? 1 : 0.5;
  const yTicks: number[] = [];
  for (let v = Math.ceil(yMin / tickStep) * tickStep; v <= yMax; v += tickStep) {
    yTicks.push(Number(v.toFixed(1)));
  }
  // Collections cluster minutes apart; keep only labels at least 70px apart
  // and clamp them inside the plot so neighbors never collide.
  const xLabels: { time: string; px: number }[] = [];
  for (const time of times) {
    const px = Math.min(Math.max(x(time), pad.left + 24), width - pad.right - 24);
    const prev = xLabels[xLabels.length - 1];
    if (!prev || px - prev.px >= 70) xLabels.push({ time, px });
  }
  const shown = books.filter((book) => book.points.length > 0);

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Strikeout line movement by bookmaker">
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} stroke="hsl(217 33% 17.5%)" strokeWidth={1} />
            <text x={pad.left - 6} y={y(tick) + 3} textAnchor="end" fontSize={9} fill="hsl(215 20% 65%)" fontFamily="var(--font-geist-mono)">
              {tick}
            </text>
          </g>
        ))}
        {xLabels.map(({ time, px }) => (
          <text key={time} x={px} y={height - 10} textAnchor="middle" fontSize={9} fill="hsl(215 20% 65%)" fontFamily="var(--font-geist-mono)">
            {fmtTimeET(time)}
          </text>
        ))}
        {shown.map((book, seriesIndex) => {
          const { color } = bookMeta(book.bookmakerId);
          // Tiny fixed per-series offset so books sitting on the same line stay
          // individually visible; ~1px against a 0.5-K grid step, cosmetic only.
          const dodge = (seriesIndex - (shown.length - 1) / 2) * 1.4;
          const pts = book.points.map((p) => ({ p, px: x(p.capturedAt), py: y(p.line) + dodge }));
          const path = pts
            .map((pt, i) => (i === 0 ? `M ${pt.px} ${pt.py}` : `H ${pt.px} V ${pt.py}`))
            .join(" ");
          return (
            <g key={book.bookmakerId}>
              <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
              {pts.map(({ p, px, py }) => (
                <g key={p.capturedAt}>
                  <circle cx={px} cy={py} r={3.5} fill={color} stroke={SURFACE} strokeWidth={2} />
                  <circle cx={px} cy={py} r={10} fill="transparent">
                    <title>
                      {`${bookMeta(book.bookmakerId).label} · ${p.line} Ks · o ${fmtOdds(p.overOdds)} / u ${fmtOdds(p.underOdds)} · ${fmtTimeET(p.capturedAt)} ET`}
                    </title>
                  </circle>
                </g>
              ))}
            </g>
          );
        })}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
        {shown.map((book) => (
          <span key={book.bookmakerId} className="flex items-center gap-1.5 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
            <span className="size-2 rounded-full" style={{ backgroundColor: bookMeta(book.bookmakerId).color }} />
            {bookMeta(book.bookmakerId).label}
          </span>
        ))}
      </div>
    </div>
  );
}

function AlternatesTable({ books }: { books: BookView[] }) {
  const withAlts = books.filter((book) => book.alternates.length > 0);
  const altLines = [...new Set(withAlts.flatMap((book) => book.alternates.map((r) => r.line)))].sort(
    (a, b) => a - b,
  );
  if (altLines.length === 0) {
    return (
      <p className="py-6 text-center font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        No alternate lines observed
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse font-mono text-[11px]">
        <thead>
          <tr className="text-left text-[9px] uppercase tracking-wider text-muted-foreground">
            <th className="py-1.5 pr-3 font-medium">Alt line</th>
            {withAlts.map((book) => (
              <th key={book.bookmakerId} className="px-2 py-1.5 text-center font-medium">
                <span className="mr-1 inline-block size-1.5 rounded-full align-middle" style={{ backgroundColor: bookMeta(book.bookmakerId).color }} />
                {bookMeta(book.bookmakerId).label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {altLines.map((line) => (
            <tr key={line} className="border-t border-border/60">
              <td className="py-1.5 pr-3 font-semibold text-foreground">{line}</td>
              {withAlts.map((book) => {
                const rung = book.alternates.find((r) => r.line === line);
                return (
                  <td key={book.bookmakerId} className="px-2 py-1.5 text-center text-muted-foreground">
                    {rung ? (
                      <>
                        <span className="text-foreground">o {fmtOdds(rung.overOdds)}</span>
                        {" / "}
                        <span>u {fmtOdds(rung.underOdds)}</span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PitcherRow({
  pitcher,
  columns,
  date,
  generatedAt,
}: {
  pitcher: PitcherEntry;
  columns: string[];
  date: string;
  generatedAt: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showAlts, setShowAlts] = useState(false);
  const [history, setHistory] = useState<HistoryResponse | "loading" | "error" | null>(null);

  // Fetch while expanded, and refetch when the board snapshot changes so a
  // board refresh can't leave an open chart showing stale collections.
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    setHistory("loading");
    const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
    fetch(
      `${baseUrl}/api/mlb/pitcher-k-odds/history/${encodeURIComponent(pitcher.playerId)}?date=${date}&event_id=${encodeURIComponent(pitcher.eventId)}`,
      { cache: "no-store" },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error(`History returned ${response.status}`);
        const payload = (await response.json()) as HistoryResponse;
        if (!cancelled) setHistory(payload);
      })
      .catch((error) => {
        console.error("Failed to fetch pitcher history:", error);
        if (!cancelled) setHistory("error");
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, generatedAt, date, pitcher.playerId, pitcher.eventId]);

  const consensus = consensusLine(pitcher.books, "current");
  const firstConsensus = consensusLine(pitcher.books, "first");
  const best = bestPrices(pitcher.books, consensus);
  const moved = consensus !== null && firstConsensus !== null && consensus !== firstConsensus;
  const byBook = new Map(pitcher.books.map((book) => [book.bookmakerId, book]));
  const altCount = pitcher.books.reduce((n, book) => n + book.alternates.length, 0);

  return (
    <>
      <tr
        className="cursor-pointer border-t border-border/60 transition-colors hover:bg-secondary/40"
        onClick={() => setExpanded((value) => !value)}
      >
        <td className="py-2.5 pl-3 pr-2">
          <div className="flex items-center gap-2">
            <ChevronDown className={`size-3.5 shrink-0 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`} />
            <div>
              <p className="text-sm font-medium text-foreground">{pitcher.playerName}</p>
              <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                {pitcher.playerTeam} vs {pitcher.opponentTeam}
              </p>
            </div>
            <PhaseBadge phase={pitcher.marketPhase} />
          </div>
        </td>
        {columns.map((bookId) => {
          const book = byBook.get(bookId);
          if (!book) {
            return (
              <td key={bookId} className="px-2 py-2.5 text-center font-mono text-xs text-muted-foreground/50">
                —
              </td>
            );
          }
          const atConsensus = book.current.line === consensus;
          const isBestOver = atConsensus && best.over !== null && book.current.overOdds === best.over.odds;
          const isBestUnder = atConsensus && best.under !== null && book.current.underOdds === best.under.odds;
          return (
            <td key={bookId} className="px-2 py-2.5 text-center">
              <p className="font-mono text-sm font-semibold text-foreground">{book.current.line}</p>
              <p className="whitespace-nowrap font-mono text-[10px] text-muted-foreground">
                <span className={isBestOver ? "font-semibold text-emerald-400" : ""}>o {fmtOdds(book.current.overOdds)}</span>
                {" · "}
                <span className={isBestUnder ? "font-semibold text-emerald-400" : ""}>u {fmtOdds(book.current.underOdds)}</span>
              </p>
            </td>
          );
        })}
        <td className="px-2 py-2.5 text-center">
          <p className="whitespace-nowrap font-mono text-[10px] text-foreground">
            O {best.over ? `${fmtOdds(best.over.odds)} ${bookMeta(best.over.bookmakerId).label}` : "—"}
          </p>
          <p className="whitespace-nowrap font-mono text-[10px] text-foreground">
            U {best.under ? `${fmtOdds(best.under.odds)} ${bookMeta(best.under.bookmakerId).label}` : "—"}
          </p>
        </td>
        <td className="px-3 py-2.5 text-center">
          {moved ? (
            <span className="inline-flex items-center gap-1 whitespace-nowrap font-mono text-xs text-amber-400">
              {firstConsensus}
              <ArrowRight className="size-3" />
              {consensus}
            </span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">{consensus ?? "—"}</span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-border/40 bg-secondary/20">
          <td colSpan={columns.length + 3} className="px-4 py-4">
            <div className="mb-2 flex items-center justify-between">
              <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                Main line history · consensus {firstConsensus ?? "—"} → {consensus ?? "—"}
              </p>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setShowAlts((value) => !value);
                }}
                className="flex items-center gap-1.5 rounded-md bg-secondary px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
              >
                <Layers className="size-3" />
                Alternates ({altCount})
              </button>
            </div>
            {history === "loading" || history === null ? (
              <div className="flex items-center justify-center py-10 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                <div className="mr-2 size-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                Loading line history
              </div>
            ) : history === "error" ? (
              <p className="py-8 text-center font-mono text-[10px] uppercase tracking-wider text-rose-300">
                Line history unavailable
              </p>
            ) : (
              <HistoryChart books={history.books} />
            )}
            {showAlts && (
              <div className="mt-4 rounded-md border border-border bg-background/60 p-3">
                <p className="mb-1 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                  Alternate ladders · latest snapshot
                </p>
                <AlternatesTable books={pitcher.books} />
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function PitcherStrikeoutOddsPage() {
  const [board, setBoard] = useState<BoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchBoard = useCallback(async (isRefresh = false) => {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
      const response = await fetch(`${baseUrl}/api/mlb/pitcher-k-odds/board`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Board returned ${response.status}`);
      setBoard((await response.json()) as BoardResponse);
    } catch (fetchError) {
      console.error("Failed to fetch pitcher-K board:", fetchError);
      setError("The strikeout odds board is unavailable. Try again in a moment.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchBoard();
  }, [fetchBoard]);

  const games = useMemo(() => {
    if (!board) return [];
    const byEvent = new Map<string, PitcherEntry[]>();
    for (const pitcher of board.pitchers) {
      byEvent.set(pitcher.eventId, [...(byEvent.get(pitcher.eventId) ?? []), pitcher]);
    }
    return [...byEvent.values()]
      .map((pitchers) => ({
        eventId: pitchers[0]!.eventId,
        matchup: `${pitchers[0]!.awayTeam} @ ${pitchers[0]!.homeTeam}`,
        startsAt: pitchers[0]!.startsAt,
        pitchers: [...pitchers].sort((a, b) => a.playerName.localeCompare(b.playerName)),
      }))
      .sort((a, b) => (a.startsAt ?? "").localeCompare(b.startsAt ?? "") || a.matchup.localeCompare(b.matchup));
  }, [board]);

  const columns = useMemo(() => {
    if (!board) return [];
    const ids = new Set<string>();
    for (const pitcher of board.pitchers) {
      for (const book of pitcher.books) ids.add(book.bookmakerId);
    }
    return [...ids].sort((a, b) => {
      const [ai, an] = bookSortKey(a);
      const [bi, bn] = bookSortKey(b);
      return ai - bi || an.localeCompare(bn);
    });
  }, [board]);

  return (
    <main className="min-h-screen bg-background px-4 py-6">
      <div className="mx-auto max-w-6xl">
        <header className="mb-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Link
                href="/"
                aria-label="Back to sports terminal"
                className="grid size-8 place-items-center rounded-md bg-secondary text-muted-foreground transition-colors hover:text-foreground"
              >
                <ArrowLeft className="size-4" />
              </Link>
              <div>
                <div className="flex items-center gap-2">
                  <Crosshair className="size-5 text-primary" />
                  <h1 className="font-sans text-lg font-bold text-foreground">Oh-K Corral</h1>
                </div>
                <p className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                  MLB pitcher strikeout odds
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              {board?.stale && (
                <span className="flex items-center gap-1 rounded-sm bg-amber-500/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest text-amber-400">
                  <ShieldAlert className="size-3" />
                  Stale — refresh failed
                </span>
              )}
              {board && (
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  {new Date(`${board.date}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                  {" · "}
                  {board.pitchers.length} pitchers
                  {board.lastObservedAt ? ` · observed ${fmtTimeET(board.lastObservedAt)} ET` : ""}
                </span>
              )}
              <button
                type="button"
                onClick={() => void fetchBoard(true)}
                disabled={refreshing || loading}
                className="flex h-8 items-center gap-2 rounded-md bg-secondary px-3 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
              >
                <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
                Refresh
              </button>
            </div>
          </div>
          <div className="mt-4 h-px w-full bg-border" />
        </header>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-28 font-mono text-xs text-muted-foreground">
            <div className="mb-3 size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            LOADING STRIKEOUT BOARD...
          </div>
        ) : error || !board ? (
          <div className="rounded-lg border border-rose-500/20 bg-rose-500/5 px-6 py-12 text-center">
            <ShieldAlert className="mx-auto mb-3 size-6 text-rose-400" />
            <p className="font-mono text-xs text-rose-300">{error}</p>
            <button
              onClick={() => void fetchBoard(true)}
              className="mt-4 font-mono text-[10px] uppercase tracking-wider text-muted-foreground hover:text-foreground"
            >
              Try again
            </button>
          </div>
        ) : games.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 font-mono text-xs uppercase text-muted-foreground">
            No pitcher strikeout lines observed today yet
          </div>
        ) : (
          <div className="space-y-5">
            {games.map((game) => (
              <section key={game.eventId} className="overflow-hidden rounded-lg border border-border bg-card">
                <div className="flex items-center justify-between border-b border-border bg-secondary/30 px-4 py-2">
                  <h2 className="font-mono text-xs font-semibold uppercase tracking-wider text-foreground">
                    {game.matchup}
                  </h2>
                  <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    {game.startsAt ? `${fmtTimeET(game.startsAt)} ET` : ""}
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] border-collapse">
                    <thead>
                      <tr className="text-[9px] uppercase tracking-wider text-muted-foreground">
                        <th className="py-2 pl-3 pr-2 text-left font-mono font-medium">Pitcher</th>
                        {columns.map((bookId) => (
                          <th key={bookId} className="px-2 py-2 text-center font-mono font-medium">
                            <span className="mr-1 inline-block size-1.5 rounded-full align-middle" style={{ backgroundColor: bookMeta(bookId).color }} />
                            {bookMeta(bookId).label}
                          </th>
                        ))}
                        <th className="px-2 py-2 text-center font-mono font-medium">Best</th>
                        <th className="px-3 py-2 text-center font-mono font-medium">Move</th>
                      </tr>
                    </thead>
                    <tbody>
                      {game.pitchers.map((pitcher) => (
                        <PitcherRow
                          key={`${pitcher.eventId}:${pitcher.playerId}`}
                          pitcher={pitcher}
                          columns={columns}
                          date={board.date}
                          generatedAt={board.generatedAt}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
          </div>
        )}

        <footer className="mt-8 border-t border-border pt-3 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
          Observed snapshots, not authoritative closes · best price highlighted at the consensus line · for entertainment only
        </footer>
      </div>
    </main>
  );
}
