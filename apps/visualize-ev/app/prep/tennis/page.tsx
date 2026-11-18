"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  TENNIS_FLAG_LABELS,
  TENNIS_GROUPS,
  TENNIS_STATS,
  TENNIS_UNPRICED_LABELS,
  devigAmerican,
  extractTennisBoard,
  groupTennisResults,
  parseAmericanOdds,
  tennisManualMatches,
  tennisProbability,
  type TennisBoard,
  type TennisGroup,
  type TennisManualLine,
  type TennisManualMatch,
  type TennisOddsType,
  type TennisPricedStat,
  type TennisResult,
  type TennisRun,
} from "@repo/ui";

const SECRET_KEY = "prep-upload-secret";
const POLL_MS = 5000;
const BOARD_STALE_MS = 60 * 60 * 1000;
const STAT_LABELS: Record<TennisPricedStat, string> = {
  "Fantasy Score": "Fantasy Score",
  "Total Games": "Total Games",
  "Total Games Won": "Games Won",
};
const TYPE_FILTERS: (TennisOddsType | "all")[] = ["standard", "goblin", "demon", "all"];
const TOUR_FILTERS = ["all", "atp", "wta"] as const;
// Flags that undercut a number, shown in the accent color so they read before the row is opened.
const WARNING_FLAGS = new Set(["assumed_aces", "stale_odds", "fit_miss", "fails_bias_check", "ml_only", "over_only", "manual_odds"]);

function readSecret(): string {
  try {
    return window.localStorage.getItem(SECRET_KEY) ?? "";
  } catch {
    return "";
  }
}

function storeSecret(value: string) {
  try {
    if (value) window.localStorage.setItem(SECRET_KEY, value);
    else window.localStorage.removeItem(SECRET_KEY);
  } catch {
    // Storage can be unavailable; the page just asks again next time.
  }
}

const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const dayTime = (iso: string) =>
  new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const pct = (value: number | null | undefined) => (value == null ? "--" : `${(value * 100).toFixed(1)}%`);
const american = (value: number | undefined) => (value == null ? "--" : value > 0 ? `+${value}` : String(value));
const num = (value: number | null | undefined) => (value == null ? "--" : String(value));

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function api(path: string, secret: string, init?: RequestInit): Promise<TennisRun> {
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/tennis${path}`, {
    ...init,
    headers: { ...init?.headers, Authorization: `Bearer ${secret}` },
  });
  if (!response.ok) {
    let detail = `The API answered ${response.status}.`;
    if (response.status === 401) detail = "The upload secret was rejected.";
    else if (response.status === 503) detail = "The API isn't configured for uploads yet (secret or run storage missing).";
    else if (response.status === 413) detail = "The board is larger than the API accepts.";
    else {
      try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = body.detail;
        else if (Array.isArray(body.detail) && body.detail[0]?.msg) {
          detail = `A row was rejected: ${body.detail[0].msg} (${(body.detail[0].loc ?? []).join(".")}).`;
        }
      } catch {
        // Keep the status-only message.
      }
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as TennisRun;
}

export default function TennisPrepPage() {
  const [secret, setSecret] = useState("");
  const [secretDraft, setSecretDraft] = useState("");
  const [text, setText] = useState("");
  const [board, setBoard] = useState<TennisBoard | null>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [run, setRun] = useState<TennisRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(true);
  const [stat, setStat] = useState<TennisPricedStat>("Fantasy Score");
  const [oddsType, setOddsType] = useState<TennisOddsType | "all">("standard");
  const [tour, setTour] = useState<(typeof TOUR_FILTERS)[number]>("all");
  const [now, setNow] = useState<Date | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const showRun = useCallback((next: TennisRun) => {
    setRun(next);
    setUploadOpen(false);
    try {
      window.history.replaceState(null, "", `?run=${next.run_id}`);
    } catch {
      // The run still shows; it just can't be reopened from the address bar.
    }
  }, []);

  // Start times are compared with the clock again on screen: a board can sit open past a start.
  useEffect(() => {
    setNow(new Date());
    const timer = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const stored = readSecret();
    setSecret(stored);
    const runId = new URLSearchParams(window.location.search).get("run");
    if (stored && runId) {
      api(`/runs/${encodeURIComponent(runId)}`, stored).then(showRun).catch((err: Error) => setError(err.message));
    }
  }, [showRun]);

  // While odds are refreshing, poll, and move to the repriced run when it exists.
  const waiting = run != null && (run.status === "pending" || run.refresh?.status === "running" || run.newer_run_id != null);
  useEffect(() => {
    if (!run || !waiting || !secret) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const next = await api(`/runs/${run.newer_run_id ?? run.run_id}`, secret);
        if (!cancelled) showRun(next);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    };
    const timer = setTimeout(tick, run.newer_run_id ? 0 : POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [run, waiting, secret, showRun]);

  function loadBoard(raw: string, when: Date) {
    setText(raw);
    setError(null);
    if (!raw.trim()) {
      setBoard(null);
      setParseError(null);
      return;
    }
    const parsed = extractTennisBoard(raw);
    setBoard(parsed.ok ? parsed.board : null);
    setParseError(parsed.ok ? null : parsed.error);
    setSavedAt(when);
  }

  async function loadFile(file: File | undefined) {
    if (file) loadBoard(await file.text(), new Date(file.lastModified));
  }

  async function submit() {
    if (!board || !savedAt) return;
    setBusy(true);
    setError(null);
    try {
      showRun(await api("/runs", secret, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ board_saved_at: savedAt.toISOString(), rows: board.rows }),
      }));
      setText("");
      setBoard(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        storeSecret("");
        setSecret("");
      }
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!run) return;
    setError(null);
    try {
      showRun(await api(`/runs/${run.run_id}/refresh`, secret, { method: "POST" }));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  /** Sends the whole set of typed-in lines; the answer is the repriced run. */
  async function saveManualLines(lines: TennisManualLine[]): Promise<boolean> {
    if (!run) return false;
    setError(null);
    try {
      showRun(await api(`/runs/${run.run_id}/manual-lines`, secret, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        // The API reads each opponent from the board.
        body: JSON.stringify({ lines: lines.map((line) => ({ ...line, opponent: undefined })) }),
      }));
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    }
  }

  const visible = useMemo(
    () => (run?.results ?? []).filter((r) =>
      r.stat === stat && (oddsType === "all" || r.odds_type === oddsType) && (tour === "all" || r.tour === tour || !r.tour)),
    [run, stat, oddsType, tour],
  );
  const groups = useMemo(
    () => (now ? groupTennisResults(visible, now, oddsType === "all") : null),
    [visible, now, oddsType],
  );
  const statCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of run?.results ?? []) counts[r.stat] = (counts[r.stat] ?? 0) + 1;
    return counts;
  }, [run]);

  return (
    <main className="min-h-screen bg-background px-3 py-5 font-sans sm:px-4 sm:py-6">
      <div className="mx-auto max-w-6xl">
        <header className="mb-4 flex flex-wrap items-end justify-between gap-3 border-b border-border pb-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-primary">Sports Analytics Terminal</p>
            <h1 className="mt-1 text-2xl font-bold text-foreground">Tennis Board</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Paste a PrizePicks tennis board to price Fantasy Score, Total Games and Games Won against the books.
            </p>
          </div>
          <div className="flex gap-4 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
            <Link href="/prep" className="transition-colors hover:text-foreground">Slate Prep</Link>
            <Link href="/" className="transition-colors hover:text-foreground">← Dashboard</Link>
          </div>
        </header>

        {!secret ? (
          <form
            className="rounded-md border border-border bg-card p-4"
            onSubmit={(event) => {
              event.preventDefault();
              const value = secretDraft.trim();
              if (!value) return;
              storeSecret(value);
              setSecret(value);
              setSecretDraft("");
              setError(null);
            }}
          >
            <label htmlFor="secret" className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
              Upload secret
            </label>
            <div className="mt-2 flex gap-2">
              <input
                id="secret"
                type="password"
                autoComplete="off"
                value={secretDraft}
                onChange={(event) => setSecretDraft(event.target.value)}
                className="min-w-0 flex-1 rounded-sm border border-border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-primary"
              />
              <button type="submit" className="rounded-sm bg-primary px-4 font-mono text-xs font-bold uppercase tracking-wider text-primary-foreground">
                Save
              </button>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">Entered once and kept in this browser.</p>
          </form>
        ) : uploadOpen || !run ? (
          <section
            className="rounded-md border border-border bg-card p-4"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              void loadFile(event.dataTransfer.files[0]);
            }}
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">Board JSON</h2>
              <div className="flex gap-4 font-mono text-[11px] uppercase tracking-wider">
                <button type="button" onClick={() => fileInput.current?.click()} className="text-primary hover:underline">
                  Choose file
                </button>
                {run && (
                  <button type="button" onClick={() => setUploadOpen(false)} className="text-muted-foreground hover:text-foreground">
                    Cancel
                  </button>
                )}
              </div>
            </div>
            <input ref={fileInput} type="file" accept=".json,application/json,text/plain" className="hidden"
              onChange={(event) => void loadFile(event.target.files?.[0])} />
            <textarea
              value={text}
              onChange={(event) => loadBoard(event.target.value, new Date())}
              placeholder="Open the PrizePicks projections URL while signed in, select all, copy, and paste here. Or drop the saved file."
              spellCheck={false}
              className="mt-2 h-28 w-full resize-y rounded-sm border border-border bg-background p-3 font-mono text-xs text-foreground outline-none placeholder:text-muted-foreground focus:border-primary"
            />
            {parseError && <p className="mt-2 text-sm text-red-400">{parseError}</p>}
            {board && (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <p className="font-mono text-xs text-foreground">
                  {board.counts["Fantasy Score"]} Fantasy Score, {board.counts["Total Games"]} Total Games,{" "}
                  {board.counts["Total Games Won"]} Games Won lines
                  <span className="text-muted-foreground">
                    ; {board.doublesSkipped} doubles, {board.notPreGameSkipped} not pre-game skipped
                  </span>
                </p>
                <button type="button" onClick={submit} disabled={busy}
                  className="rounded-sm bg-primary px-4 py-2 font-mono text-xs font-bold uppercase tracking-wider text-primary-foreground disabled:opacity-50">
                  {busy ? "Pricing…" : "Price board"}
                </button>
              </div>
            )}
          </section>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card px-4 py-2 font-mono text-xs">
            <span className="text-muted-foreground">
              {run.results.length === 0
                ? "Board uploaded; waiting for odds"
                : TENNIS_STATS.map((s) => `${statCounts[s] ?? 0} ${STAT_LABELS[s]}`).join(" · ")}
            </span>
            <button type="button" onClick={() => setUploadOpen(true)} className="uppercase tracking-wider text-primary hover:underline">
              New board
            </button>
          </div>
        )}

        {error && (
          <p role="alert" className="mt-3 rounded-md border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300">{error}</p>
        )}

        {run && now && <StatusLine run={run} now={now} onRetry={retry} />}

        {run && run.status === "ready" && now && !run.newer_run_id && (
          <ManualLines run={run} now={now} onSave={saveManualLines} />
        )}

        {run && run.status === "ready" && groups && (
          <>
            <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2">
              <Tabs label="Stat" options={TENNIS_STATS} value={stat} onChange={setStat}
                render={(s) => `${STAT_LABELS[s]} ${statCounts[s] ?? 0}`} />
              <Tabs label="Type" options={TYPE_FILTERS} value={oddsType} onChange={setOddsType} render={(t) => t} />
              <Tabs label="Tour" options={TOUR_FILTERS} value={tour} onChange={setTour} render={(t) => t} />
            </div>
            <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              Ordered by model probability{oddsType === "all" ? ", within each odds type" : ""}. A lean is a direction, not a value call.
            </p>
            {visible.length === 0 ? (
              <p className="mt-4 rounded-md border border-border bg-card px-4 py-8 text-center font-mono text-xs text-muted-foreground">
                No {STAT_LABELS[stat]} lines match these filters.
              </p>
            ) : (
              TENNIS_GROUPS.map((group) => groups[group.id].length > 0 && (
                <ResultGroup key={group.id} group={group} rows={groups[group.id]} stat={stat} />
              ))
            )}
          </>
        )}
      </div>
    </main>
  );
}

function StatusLine({ run, now, onRetry }: { run: TennisRun; now: Date; onRetry: () => void }) {
  const boardAge = now.getTime() - new Date(run.board_saved_at).getTime();
  const refresh = run.refresh;
  const elapsed = refresh ? Math.max(0, Math.round((now.getTime() - new Date(refresh.started_at).getTime()) / 1000)) : 0;
  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-muted-foreground">
        <span>Board copied <span className="text-foreground">{clock(run.board_saved_at)}</span></span>
        <span>
          Odds{" "}
          <span className="text-foreground">{run.odds_snapshot_at ? clock(run.odds_snapshot_at) : "none yet"}</span>
          {run.odds_age_s != null && run.odds_age_s > 20 * 60 && (
            <span className="text-primary"> ({Math.round(run.odds_age_s / 60)} min old when priced)</span>
          )}
        </span>
      </div>
      {refresh?.status === "running" && (
        <p className="rounded-md border border-primary/40 bg-primary/10 px-4 py-2 font-mono text-xs text-primary">
          <span className="mr-2 inline-block size-1.5 animate-pulse rounded-full bg-primary align-middle" />
          Refreshing odds: {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")} elapsed (usually 2 to 3 minutes).
          {run.status === "pending" ? " Prices appear when it lands." : " These prices are on older odds until it lands."}
        </p>
      )}
      {refresh?.status === "failed" && !run.newer_run_id && (
        <p className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-300">
          <span>The odds refresh failed: {refresh.error ?? "unknown error"}.</span>
          <button type="button" onClick={onRetry} className="rounded-sm border border-red-400/60 px-3 py-1 font-mono text-xs uppercase tracking-wider hover:bg-red-500/20">
            Retry
          </button>
        </p>
      )}
      {boardAge > BOARD_STALE_MS && (
        <p className="rounded-md border border-border bg-secondary px-4 py-2 text-sm text-foreground">
          This board was copied {Math.round(boardAge / 60000)} minutes ago. PrizePicks moves lines too: copy it again before playing anything.
        </p>
      )}
    </div>
  );
}

function Tabs<T extends string>({ label, options, value, onChange, render }: {
  label: string; options: readonly T[]; value: T; onChange: (value: T) => void; render: (value: T) => string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <div className="flex flex-wrap gap-1 rounded-md bg-secondary p-1">
        {options.map((option) => (
          <button key={option} type="button" onClick={() => onChange(option)} aria-pressed={value === option}
            className={`h-7 whitespace-nowrap rounded-sm px-2.5 font-mono text-[11px] uppercase tracking-wider transition-colors ${
              value === option ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
            }`}>
            {render(option)}
          </button>
        ))}
      </div>
    </div>
  );
}

function Lean({ row, group }: { row: TennisResult; group: TennisGroup }) {
  const p = tennisProbability(row);
  if (row.unpriced_reason) {
    return <span className="text-muted-foreground">{TENNIS_UNPRICED_LABELS[row.unpriced_reason]}</span>;
  }
  const live = group === "actionable" || group === "provisional";
  const strong = live && p != null && p >= 0.6;
  return (
    <span className="whitespace-nowrap">
      <span className={live ? "font-bold text-foreground" : "text-muted-foreground"}>
        {row.lean ? (row.lean === "over" ? "▲ OVER" : "▼ UNDER") : "no lean"}
      </span>{" "}
      <span className={`tabular-nums ${strong ? "font-bold text-emerald-400" : live ? "text-foreground" : "text-muted-foreground"}`}>{pct(p)}</span>
      {!!row.push && <span className="text-muted-foreground"> · push {pct(row.push)}</span>}
    </span>
  );
}

function Flags({ row }: { row: TennisResult }) {
  if (row.flags.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {row.flags.map((flag) => (
        <span key={flag} className={`whitespace-nowrap rounded-sm border px-1.5 py-0.5 text-[10px] uppercase leading-none tracking-wider ${
          WARNING_FLAGS.has(flag) ? "border-primary/50 text-primary" : "border-border text-muted-foreground"
        }`}>
          {TENNIS_FLAG_LABELS[flag] ?? flag}
        </span>
      ))}
    </span>
  );
}

const subject = (row: TennisResult) => (row.stat === "Total Games" ? `${row.player} vs ${row.opponent}` : row.player);

function dkAtLine(row: TennisResult) {
  if (row.dk_p_over != null) return pct(row.dk_p_over);
  if (row.dk_p_over_lo != null && row.dk_p_over_hi != null) return `${pct(row.dk_p_over_lo)} / ${pct(row.dk_p_over_hi)}`;
  return "--";
}

const dkGamesWon = (row: TennisResult) =>
  row.dk_gw_line == null ? "--" : `${row.dk_gw_line} o${american(row.dk_gw_over)} / u${american(row.dk_gw_under)}`;

/** The numbers behind a row: book comparison, bias check, rule of thumb, ace source, fit. */
function details(row: TennisResult): [string, string][] {
  const out: [string, string][] = [];
  if (row.p_over != null) out.push(["P over / under", `${pct(row.p_over)} / ${pct(row.p_under)}`]);
  if (row.p_win != null) out.push(["Win probability", pct(row.p_win)]);
  if (row.stat === "Fantasy Score") {
    out.push(["Model mean / median", `${num(row.model_mean)} / ${num(row.model_median)}`]);
    if (row.p_side_bias != null) out.push(["P with bias applied", pct(row.p_side_bias)]);
    out.push(["Rule of thumb FS", num(row.rule_fs)], ["Aces source", row.aces_src ?? "--"], ["DK spread", num(row.spread)]);
  } else {
    out.push(["Model mean", num(row.model_mean)]);
    if (row.stat === "Total Games") out.push(["DK P over at line", dkAtLine(row)]);
    else out.push(["DK games won", dkGamesWon(row)], ["DK P over at its line", pct(row.dk_gw_p_over)]);
  }
  const typed = row.flags.includes("manual_odds");
  out.push([typed ? "Entered total" : "DK total", num(row.total)], ["Favorite moneyline", american(row.fav_ml)], ["Fit", row.fit ?? "--"]);
  return out;
}

const COLUMNS: Record<TennisPricedStat, { label: string; cell: (row: TennisResult) => string }[]> = {
  "Fantasy Score": [
    { label: "P bias", cell: (r) => pct(r.p_side_bias) },
    { label: "Rule FS", cell: (r) => num(r.rule_fs) },
    { label: "Aces", cell: (r) => r.aces_src ?? "--" },
  ],
  "Total Games": [
    { label: "DK P at line", cell: dkAtLine },
    { label: "Fav ML", cell: (r) => american(r.fav_ml) },
  ],
  "Total Games Won": [{ label: "DK line", cell: dkGamesWon }],
};

function ResultGroup({ group, rows, stat }: { group: (typeof TENNIS_GROUPS)[number]; rows: TennisResult[]; stat: TennisPricedStat }) {
  const [open, setOpen] = useState<string | null>(null);
  const dim = group.id !== "actionable" && group.id !== "provisional";
  const columns = COLUMNS[stat];
  return (
    <section className="mt-5">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3">
        <h2 className={`font-mono text-xs font-bold uppercase tracking-widest ${group.id === "actionable" ? "text-emerald-400" : group.id === "provisional" ? "text-primary" : "text-muted-foreground"}`}>
          {group.label} <span className="font-normal">{rows.length}</span>
        </h2>
        <p className="text-xs text-muted-foreground">{group.hint}</p>
      </div>

      {/* Phone: cards. */}
      <div className="space-y-2 md:hidden">
        {rows.map((row) => (
          <button key={row.projection_id} type="button" onClick={() => setOpen(open === row.projection_id ? null : row.projection_id)}
            aria-expanded={open === row.projection_id}
            className={`block w-full rounded-md border bg-card p-3 text-left font-mono text-xs ${group.id === "actionable" ? "border-emerald-500/40" : "border-border"}`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className={`font-sans text-sm font-semibold ${dim ? "text-muted-foreground" : "text-foreground"}`}>{row.player}</div>
                <div className="text-muted-foreground">vs {row.opponent}</div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-base font-bold tabular-nums text-foreground">{row.line}</div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{row.odds_type}</div>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <Lean row={row} group={group.id} />
              <span className="text-muted-foreground">{dayTime(row.start_time)}</span>
            </div>
            {row.flags.length > 0 && <div className="mt-2"><Flags row={row} /></div>}
            {open === row.projection_id && !row.unpriced_reason && <Details row={row} />}
          </button>
        ))}
      </div>

      {/* Desktop: compact table. */}
      <div className="hidden overflow-x-auto rounded-md border border-border bg-card md:block">
        <table className="w-full text-left font-mono text-xs">
          <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Start</th>
              <th className="px-3 py-2">{stat === "Total Games" ? "Match" : "Player"}</th>
              <th className="px-3 py-2 text-right">Line</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Lean</th>
              {columns.map((column) => <th key={column.label} className="px-3 py-2">{column.label}</th>)}
              <th className="px-3 py-2">Flags</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Fragment key={row.projection_id}>
                <tr onClick={() => setOpen(open === row.projection_id ? null : row.projection_id)}
                  className={`cursor-pointer border-t border-border hover:bg-secondary/60 ${group.id === "actionable" ? "shadow-[inset_3px_0_0_0_rgb(16_185_129)]" : ""}`}>
                  <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">{dayTime(row.start_time)}</td>
                  <td className={`px-3 py-2 ${dim ? "text-muted-foreground" : "text-foreground"}`}>
                    <span className="font-semibold">{subject(row)}</span>
                    {row.stat !== "Total Games" && <span className="text-muted-foreground"> vs {row.opponent}</span>}
                  </td>
                  <td className="px-3 py-2 text-right font-bold tabular-nums text-foreground">{row.line}</td>
                  <td className="px-3 py-2 uppercase text-muted-foreground">{row.odds_type}</td>
                  <td className="px-3 py-2"><Lean row={row} group={group.id} /></td>
                  {columns.map((column) => (
                    <td key={column.label} className="whitespace-nowrap px-3 py-2 tabular-nums text-muted-foreground">
                      {row.unpriced_reason ? "--" : column.cell(row)}
                    </td>
                  ))}
                  <td className="px-3 py-2"><Flags row={row} /></td>
                </tr>
                {open === row.projection_id && !row.unpriced_reason && (
                  <tr className="border-t border-border bg-background">
                    <td colSpan={6 + columns.length} className="px-3 pb-3"><Details row={row} /></td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Details({ row }: { row: TennisResult }) {
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border pt-3 sm:grid-cols-3 lg:grid-cols-5">
      {details(row).map(([label, value]) => (
        <div key={label}>
          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
          <dd className="tabular-nums text-foreground">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

type ManualDraft = Record<"mlPlayer" | "mlOpponent" | "totalLine" | "totalOver" | "totalUnder" | "acesPlayer" | "acesOpponent", string>;

const asText = (value: number | null | undefined, signed = false) =>
  value == null ? "" : signed && value > 0 ? `+${value}` : String(value);

function draftFrom(line: TennisManualLine | null): ManualDraft {
  return {
    mlPlayer: asText(line?.ml_player, true), mlOpponent: asText(line?.ml_opponent, true),
    totalLine: asText(line?.total_line), totalOver: asText(line?.total_over, true), totalUnder: asText(line?.total_under, true),
    acesPlayer: asText(line?.aces_player), acesOpponent: asText(line?.aces_opponent),
  };
}

/** The line a draft describes, or what is wrong with it. */
function readDraft(match: TennisManualMatch, tour: "atp" | "wta" | null, draft: ManualDraft): TennisManualLine | string {
  const price = (value: string) => (value.trim() ? parseAmericanOdds(value) : undefined);
  const count = (value: string) => (value.trim() ? Number(value) : undefined);
  if (!tour) return "Pick the tour.";
  const mlPlayer = price(draft.mlPlayer), mlOpponent = price(draft.mlOpponent);
  if (mlPlayer == null || mlOpponent == null) return "Both moneylines are needed, as American odds (-180, +150).";
  if (Math.min(mlPlayer, mlOpponent) > 120) return "Both moneylines are underdog prices; check the signs.";
  const totalLine = count(draft.totalLine), over = price(draft.totalOver), under = price(draft.totalUnder);
  if (totalLine !== undefined && !(totalLine >= 15 && totalLine < 30 && Number.isInteger(totalLine * 2))) {
    return "The total should be between 15 and 29.5, in half games.";
  }
  if (over === null || under === null) return "Total prices are American odds (-110).";
  if ((over === undefined) !== (under === undefined)) return "Give both total prices, or leave both blank for even.";
  if (over !== undefined && totalLine === undefined) return "Total prices need a total line.";
  const aces = [count(draft.acesPlayer), count(draft.acesOpponent)];
  if (aces.some((value) => value !== undefined && !(value >= 0 && value <= 40))) return "Aces should be between 0 and 40.";
  return {
    game_id: match.game_id, player: match.player, tour, ml_player: mlPlayer, ml_opponent: mlOpponent,
    total_line: totalLine ?? null, total_over: over ?? null, total_under: under ?? null,
    aces_player: aces[0] ?? null, aces_opponent: aces[1] ?? null,
  };
}

function ManualLines({ run, now, onSave }: { run: TennisRun; now: Date; onSave: (lines: TennisManualLine[]) => Promise<boolean> }) {
  const matches = useMemo(() => tennisManualMatches(run, now), [run, now]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  if (matches.length === 0) return null;
  const missing = matches.filter((match) => !match.entered).length;
  const entered = matches.length - missing;

  const save = async (line: TennisManualLine | null, gameId: string) => {
    const others = run.manual_lines.filter((existing) => existing.game_id !== gameId);
    const saved = await onSave(line ? [...others, line] : others);
    if (saved) setEditing(null);
    return saved;
  };

  return (
    <section className="mt-3 rounded-md border border-border bg-card font-mono text-xs">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex w-full flex-wrap items-center justify-between gap-2 px-4 py-2 text-left">
        <span className="text-muted-foreground">
          {missing > 0 && <span className="text-foreground">{missing} {missing === 1 ? "match has" : "matches have"} no book line</span>}
          {missing > 0 && entered > 0 && " · "}
          {entered > 0 && <span className="text-primary">{entered} entered by hand</span>}
        </span>
        <span className="uppercase tracking-wider text-primary">{open ? "Hide" : "Enter lines"}</span>
      </button>
      {open && (
        <ul className="border-t border-border">
          {matches.map((match) => (
            <li key={match.game_id} className="border-b border-border last:border-b-0">
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2">
                <span>
                  <span className="font-sans text-sm font-semibold text-foreground">{match.player}</span>
                  <span className="text-muted-foreground"> vs {match.opponent} · {dayTime(match.start_time)}</span>
                </span>
                <span className="flex items-center gap-3">
                  {match.entered && (
                    <span className="text-muted-foreground">
                      {american(match.entered.ml_player)} / {american(match.entered.ml_opponent)}
                      {match.entered.total_line != null && ` · total ${match.entered.total_line}`}
                    </span>
                  )}
                  <button type="button" onClick={() => setEditing(editing === match.game_id ? null : match.game_id)}
                    className="uppercase tracking-wider text-primary hover:underline">
                    {editing === match.game_id ? "Close" : match.entered ? "Edit" : "Enter"}
                  </button>
                </span>
              </div>
              {editing === match.game_id && <ManualLineForm match={match} onSave={save} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// Enough of a name to tell two fields apart in a narrow column.
const last = (name: string) => name.split(" ").slice(1).join(" ") || name;

function ManualLineForm({ match, onSave }: {
  match: TennisManualMatch; onSave: (line: TennisManualLine | null, gameId: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState(() => draftFrom(match.entered));
  const [tour, setTour] = useState(match.tour);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const parsed = readDraft(match, tour, draft);
  const line = typeof parsed === "string" ? null : parsed;

  const field = (key: keyof ManualDraft, label: string, placeholder: string, decimal = false) => (
    <label className="block min-w-0">
      <span className="block truncate text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <input value={draft[key]} placeholder={placeholder} inputMode={decimal ? "decimal" : "text"} autoComplete="off"
        onChange={(event) => { setDraft({ ...draft, [key]: event.target.value }); setProblem(null); }}
        className="mt-1 w-full rounded-sm border border-border bg-background px-2 py-1.5 text-sm tabular-nums text-foreground outline-none placeholder:text-muted-foreground/60 focus:border-primary" />
    </label>
  );

  const submit = async (next: TennisManualLine | null) => {
    setBusy(true);
    await onSave(next, match.game_id);
    setBusy(false);
  };

  return (
    <form className="space-y-3 border-t border-border bg-background px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (line) void submit(line);
        else setProblem(parsed as string);
      }}>
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Tour</span>
        <div className="flex gap-1 rounded-md bg-secondary p-1">
          {(["atp", "wta"] as const).map((option) => (
            <button key={option} type="button" aria-pressed={tour === option} onClick={() => { setTour(option); setProblem(null); }}
              className={`h-7 rounded-sm px-2.5 text-[11px] uppercase tracking-wider ${tour === option ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>
              {option}
            </button>
          ))}
        </div>
        <span className="text-muted-foreground">Men&apos;s Challengers are ATP.</span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {field("mlPlayer", `ML ${last(match.player)}`, "-180")}
        {field("mlOpponent", `ML ${last(match.opponent)}`, "+150")}
        {field("totalLine", "Total games", "22.5", true)}
        {field("totalOver", "Over price", "-110")}
        {field("totalUnder", "Under price", "-110")}
        {field("acesPlayer", `Aces ${last(match.player)}`, "optional", true)}
        {field("acesOpponent", `Aces ${last(match.opponent)}`, "optional", true)}
      </div>
      <p className="text-muted-foreground">
        {line ? (
          <>
            Reads as: <span className="text-foreground">{match.player} wins {pct(devigAmerican(line.ml_player, line.ml_opponent))}</span>
            {line.total_line != null
              ? <>, <span className="text-foreground">over {line.total_line} games {pct(devigAmerican(line.total_over ?? -110, line.total_under ?? -110))}</span>.</>
              : ". Without a total, Fantasy Score stays unpriced and the games markets get no lean."}
            {line.total_line != null && line.aces_player == null && line.aces_opponent == null && " Without aces, Fantasy Score uses the tour's average rate."}
          </>
        ) : "Moneylines are required. Total prices left blank count as even. Aces are the number you expect for the match."}
      </p>
      {problem && <p className="text-red-400">{problem}</p>}
      <div className="flex gap-3">
        <button type="submit" disabled={busy}
          className="rounded-sm bg-primary px-4 py-2 text-xs font-bold uppercase tracking-wider text-primary-foreground disabled:opacity-50">
          {busy ? "Pricing…" : "Price match"}
        </button>
        {match.entered && (
          <button type="button" disabled={busy} onClick={() => void submit(null)}
            className="rounded-sm border border-border px-4 py-2 text-xs uppercase tracking-wider text-muted-foreground hover:text-foreground disabled:opacity-50">
            Remove
          </button>
        )}
      </div>
    </form>
  );
}
