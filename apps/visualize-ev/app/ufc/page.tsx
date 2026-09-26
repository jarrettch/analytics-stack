"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Check, Clock, RefreshCw, Swords } from "lucide-react";
import type { UFCCardData, UFCFightData, UFCFighterData } from "@repo/ui";

const BOOK_LABELS: Record<string, string> = {
  draftkings: "DK",
  fanduel: "FD",
};
const POLL_MS = 60_000;

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; card: UFCCardData }
  | { kind: "empty" }
  | { kind: "error"; message: string };

function fmtOdds(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return value > 0 ? `+${value}` : `${value}`;
}

function fmtPct(value: number | null): string {
  return value == null ? "--" : `${(value * 100).toFixed(1)}%`;
}

function fmtTimeET(iso: string | null): string {
  if (!iso) return "unknown";
  return new Date(iso).toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function fmtCardDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function StaleTag({ lastObservedAt }: { lastObservedAt: string | null }) {
  return (
    <span
      title={`No longer offered. Last observed ${fmtTimeET(lastObservedAt)} ET`}
      className="ml-1 inline-flex items-center gap-0.5 rounded-sm bg-amber-500/10 px-1 font-mono text-[9px] uppercase tracking-wider text-amber-400"
    >
      <Clock className="size-2.5" />
      stale
    </span>
  );
}

function FighterRow({ fighter, final }: { fighter: UFCFighterData; final: boolean }) {
  const lost = final && fighter.winner === false;
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-center gap-3">
      <span
        className={`flex min-w-0 items-center gap-1.5 truncate font-sans text-sm font-semibold ${
          lost ? "text-muted-foreground" : "text-foreground"
        }`}
      >
        {fighter.winner && <Check className="size-3.5 shrink-0 text-emerald-400" />}
        <span className="truncate">{fighter.name}</span>
      </span>
      <span className="w-14 text-right font-mono text-xs tabular-nums text-foreground">
        {fmtOdds(fighter.moneyline)}
      </span>
      <span className="w-14 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
        {fmtPct(fighter.winProbability)}
      </span>
    </div>
  );
}

function FightCard({ fight }: { fight: UFCFightData }) {
  const [a, b] = fight.fighters;
  const final = fight.gameStatus === 3;
  const live = fight.gameStatus === 2;
  const total = fight.totalRounds;
  const distance = fight.goesTheDistance;

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-secondary px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider">
        <span className="text-primary">
          {fight.order === 1 ? "Main event" : `Bout ${fight.order}`}
        </span>
        <span className="truncate text-muted-foreground">{fight.weightClass}</span>
        <span
          className={
            live
              ? "animate-pulse text-emerald-400"
              : "text-muted-foreground"
          }
        >
          {fight.status}
        </span>
      </div>

      <div className="flex flex-col gap-1.5 px-3 py-2">
        <div className="grid grid-cols-[1fr_auto_auto] gap-3 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
          <span>
            {fight.bookmaker
              ? `Moneyline · ${BOOK_LABELS[fight.bookmaker] ?? fight.bookmaker}`
              : "No line"}
          </span>
          <span className="w-14 text-right">ML</span>
          <span className="w-14 text-right">Win%</span>
        </div>
        <FighterRow fighter={a} final={final} />
        <FighterRow fighter={b} final={final} />
      </div>

      <div className="grid grid-cols-2 border-t border-border font-mono text-[10px] text-muted-foreground">
        <div className="px-3 py-1.5">
          <span className="uppercase tracking-wider">Rounds </span>
          {total ? (
            <span className="text-foreground">
              {total.line} o{fmtOdds(total.overOdds)} / u{fmtOdds(total.underOdds)}
              {total.isStale && <StaleTag lastObservedAt={total.lastObservedAt} />}
            </span>
          ) : (
            "--"
          )}
        </div>
        <div className="border-l border-border px-3 py-1.5">
          <span className="uppercase tracking-wider">Distance </span>
          {distance ? (
            <span className="text-foreground">
              Y {fmtOdds(distance.yesOdds)} / N {fmtOdds(distance.noOdds)}
              {distance.isStale && <StaleTag lastObservedAt={distance.lastObservedAt} />}
            </span>
          ) : (
            "--"
          )}
        </div>
      </div>
    </div>
  );
}

export default function UFCPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
      const response = await fetch(`${baseUrl}/api/ufc/next-card`, { cache: "no-store" });
      if (response.status === 404) {
        setState({ kind: "empty" });
      } else if (!response.ok) {
        throw new Error(`UFC feed returned ${response.status}`);
      } else {
        setState({ kind: "ready", card: (await response.json()) as UFCCardData });
      }
    } catch (error) {
      // Keep showing the last good card; only an empty page shows the error.
      setState((previous) =>
        previous.kind === "ready"
          ? previous
          : { kind: "error", message: error instanceof Error ? error.message : "UFC feed unavailable" },
      );
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(interval);
  }, [load]);

  const card = state.kind === "ready" ? state.card : null;
  const priced = card?.fights.filter((fight) => fight.bookmaker).length ?? 0;

  return (
    <main className="min-h-screen bg-background px-4 py-6">
      <div className="mx-auto max-w-3xl">
        <header className="mb-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <Link
                href="/"
                aria-label="Back to sports terminal"
                className="grid size-8 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground transition-colors hover:text-foreground"
              >
                <ArrowLeft className="size-4" />
              </Link>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Swords className="size-5 shrink-0 text-primary" />
                  <h1 className="truncate font-sans text-lg font-bold text-foreground">
                    {card?.name ?? "UFC"}
                  </h1>
                </div>
                <p className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                  {card
                    ? `${fmtCardDate(card.date)} · ${card.venue}${card.broadcast ? ` · ${card.broadcast}` : ""}`
                    : "Next card"}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              {card && (
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  {card.fights.length} fights · {priced} priced
                </span>
              )}
              <button
                type="button"
                onClick={() => void load()}
                disabled={refreshing}
                className="flex h-8 items-center gap-2 rounded-md bg-secondary px-3 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
              >
                <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
                Refresh
              </button>
            </div>
          </div>
          <div className="mt-3 h-px w-full bg-border" />
        </header>

        {state.kind === "loading" && (
          <div className="flex flex-col items-center justify-center py-20 font-mono text-xs text-muted-foreground">
            <div className="mb-2 size-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            LOADING CARD...
          </div>
        )}
        {state.kind === "empty" && (
          <p className="py-20 text-center font-mono text-xs uppercase text-muted-foreground">
            No upcoming UFC card
          </p>
        )}
        {state.kind === "error" && (
          <p className="py-20 text-center font-mono text-xs uppercase text-red-400">
            {state.message}
          </p>
        )}
        {card && card.fights.length === 0 && (
          <p className="py-20 text-center font-mono text-xs uppercase text-muted-foreground">
            Bouts not announced yet
          </p>
        )}
        {card && card.fights.length > 0 && (
          <div className="flex flex-col gap-3">
            {card.fights.map((fight) => (
              <FightCard key={fight.fightId} fight={fight} />
            ))}
          </div>
        )}

        <p className="mt-6 font-mono text-[10px] text-muted-foreground">
          One book prices each fight: DraftKings when it has both moneylines, otherwise FanDuel.
          Win% is de-vigged from that book&apos;s pair. For entertainment only.
        </p>
      </div>
    </main>
  );
}
