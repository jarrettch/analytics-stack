"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  Gauge,
  RefreshCw,
  ShieldAlert,
  TimerReset,
  TrendingUp,
  Zap,
} from "lucide-react";
import { getUsageWindow } from "@repo/ui";

type LimitValue = number | "unlimited" | "n/a" | null;

type UsageInterval = {
  key: string;
  label: string;
  requests: { current: LimitValue; max: LimitValue };
  entities: { current: LimitValue; max: LimitValue };
  resetsAt: string | null;
};

type UsageResponse = {
  provider: string;
  tier: string;
  active: boolean;
  billingCycleAnchor?: string;
  billingWindowDays?: number;
  fetchedAt: string;
  cached: boolean;
  intervals: UsageInterval[];
};

function finite(value: LimitValue): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function formatNumber(value: LimitValue): string {
  if (value === "unlimited") return "UNLIMITED";
  if (value === "n/a" || value === null) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
}

function percentage(current: LimitValue, max: LimitValue): number | null {
  if (!finite(current) || !finite(max) || max <= 0) return null;
  return Math.min((current / max) * 100, 100);
}

function LimitBar({ current, max }: { current: LimitValue; max: LimitValue }) {
  const used = percentage(current, max);
  const warning = used !== null && used >= 80;
  return (
    <div>
      <div className="mb-2 flex items-end justify-between gap-3 font-mono">
        <span className="text-lg font-semibold text-foreground">{formatNumber(current)}</span>
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          of {formatNumber(max)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-secondary">
        {used === null ? (
          <div className="h-full w-full bg-muted-foreground/10" />
        ) : (
          <div
            className={`h-full rounded-full transition-[width] duration-500 ${warning ? "bg-amber-400" : "bg-sky-400"}`}
            style={{ width: `${Math.max(used, 1)}%` }}
          />
        )}
      </div>
    </div>
  );
}

export default function UsagePage() {
  const [usage, setUsage] = useState<UsageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchUsage = useCallback(async (force = false) => {
    if (force) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
      const response = await fetch(
        `${baseUrl}/api/usage/sports-game-odds${force ? "?refresh=true" : ""}`,
        { cache: "no-store" },
      );
      if (!response.ok) throw new Error(`Usage feed returned ${response.status}`);
      setUsage(await response.json() as UsageResponse);
    } catch (fetchError) {
      console.error("Failed to fetch provider usage:", fetchError);
      setError("The provider usage feed is unavailable. Try again in a moment.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchUsage();
  }, [fetchUsage]);

  const metrics = useMemo(() => {
    const monthly = usage?.intervals.find((interval) => interval.key === "per-month");
    if (!monthly) return null;
    const current = monthly.entities.current;
    const max = monthly.entities.max;
    if (!finite(current) || !finite(max)) return null;

    const now = new Date();
    const window = getUsageWindow({
      resetsAt: monthly.resetsAt,
      fallbackAnchor: usage?.billingCycleAnchor ?? "2026-04-08T00:00:00Z",
      windowDays: usage?.billingWindowDays ?? 30,
      now,
    });
    const averagePerDay = current / window.elapsedDays;
    const projected = Math.round(averagePerDay * window.totalDays);
    const remaining = Math.max(max - current, 0);
    const dailyBudget = window.remainingDays > 0 ? remaining / window.remainingDays : 0;
    const used = (current / max) * 100;
    const projectedPercent = (projected / max) * 100;
    const health = projectedPercent >= 100 ? "risk" : projectedPercent >= 80 ? "watch" : "healthy";
    return { current, max, remaining, used, projected, dailyBudget, averagePerDay, health, ...window };
  }, [usage]);

  const healthStyles = metrics?.health === "risk"
    ? { text: "text-rose-400", bg: "bg-rose-400", badge: "bg-rose-500/10 text-rose-400", label: "AT RISK" }
    : metrics?.health === "watch"
      ? { text: "text-amber-400", bg: "bg-amber-400", badge: "bg-amber-500/10 text-amber-400", label: "WATCH" }
      : { text: "text-emerald-400", bg: "bg-emerald-400", badge: "bg-emerald-500/10 text-emerald-400", label: "HEALTHY" };

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
                  <Gauge className="size-5 text-primary" />
                  <h1 className="font-sans text-lg font-bold text-foreground">Usage Monitor</h1>
                </div>
                <p className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
                  Provider quota health
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void fetchUsage(true)}
              disabled={refreshing || loading}
              className="flex h-8 items-center gap-2 rounded-md bg-secondary px-3 font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
            >
              <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
          <div className="mt-4 h-px w-full bg-border" />
        </header>

        {loading ? (
          <div className="flex flex-col items-center justify-center py-28 font-mono text-xs text-muted-foreground">
            <div className="mb-3 size-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            CHECKING PROVIDER LIMITS...
          </div>
        ) : error || !usage ? (
          <div className="rounded-lg border border-rose-500/20 bg-rose-500/5 px-6 py-12 text-center">
            <ShieldAlert className="mx-auto mb-3 size-6 text-rose-400" />
            <p className="font-mono text-xs text-rose-300">{error}</p>
            <button onClick={() => void fetchUsage(true)} className="mt-4 font-mono text-[10px] uppercase tracking-wider text-muted-foreground hover:text-foreground">
              Try again
            </button>
          </div>
        ) : (
          <>
            <section className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
              <div className="flex items-center gap-3">
                <span className={`size-2 rounded-full ${usage.active ? "bg-emerald-400 shadow-[0_0_8px_#34d399]" : "bg-rose-400"}`} />
                <div>
                  <p className="text-sm font-medium text-foreground">{usage.provider}</p>
                  <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    {usage.tier} tier · {usage.active ? "key active" : "key inactive"}
                  </p>
                </div>
              </div>
              <div className="text-left sm:text-right">
                <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Last checked</p>
                <p className="mt-0.5 font-mono text-xs text-foreground">
                  {new Date(usage.fetchedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  {usage.cached ? " · CACHED" : " · LIVE"}
                </p>
              </div>
            </section>

            {metrics && (
              <section className="mb-4 grid gap-4 lg:grid-cols-[1.35fr_1fr]">
                <div className="rounded-lg border border-border bg-card p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">30-day entities</p>
                      <div className="mt-2 flex items-baseline gap-2 font-mono">
                        <span className="text-4xl font-semibold tracking-tight text-foreground">{formatNumber(metrics.current)}</span>
                        <span className="text-sm text-muted-foreground">/ {formatNumber(metrics.max)}</span>
                      </div>
                    </div>
                    <span className={`rounded-sm px-2 py-1 font-mono text-[10px] tracking-widest ${healthStyles.badge}`}>
                      {healthStyles.label}
                    </span>
                  </div>
                  <div className="mt-6">
                    <div className="relative h-3 overflow-hidden rounded-full bg-secondary">
                      <div className={`h-full rounded-full ${healthStyles.bg}`} style={{ width: `${Math.min(metrics.used, 100)}%` }} />
                      <div
                        className="absolute inset-y-0 w-px bg-foreground/70"
                        style={{ left: `${Math.min((metrics.projected / metrics.max) * 100, 100)}%` }}
                      />
                    </div>
                    <div className="mt-2 flex justify-between font-mono text-[10px] uppercase text-muted-foreground">
                      <span>{metrics.used.toFixed(1)}% used</span>
                      <span>{formatNumber(metrics.remaining)} remaining</span>
                    </div>
                  </div>
                  <div className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-md bg-border sm:grid-cols-4">
                    {[
                      ["Daily avg", metrics.averagePerDay.toFixed(1)],
                      ["Daily budget", metrics.dailyBudget.toFixed(1)],
                      ["Projected", formatNumber(metrics.projected)],
                      ["Days left", Math.ceil(metrics.remainingDays).toString()],
                    ].map(([label, value]) => (
                      <div key={label} className="bg-background/70 px-3 py-3">
                        <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">{label}</p>
                        <p className="mt-1 font-mono text-sm font-semibold text-foreground">{value}</p>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-5">
                  <div className="flex items-center gap-2">
                    {metrics.health === "healthy" ? <CheckCircle2 className="size-4 text-emerald-400" /> : <TrendingUp className={`size-4 ${healthStyles.text}`} />}
                    <p className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">Pace analysis</p>
                  </div>
                  <p className="mt-4 text-xl font-semibold leading-snug text-foreground">
                    {metrics.health === "healthy"
                      ? "Current burn rate is within budget."
                      : metrics.health === "watch"
                        ? "Usage is trending close to the 30-day cap."
                        : "Usage is projected to exceed the 30-day cap."}
                  </p>
                  <p className="mt-3 text-sm leading-6 text-muted-foreground">
                    At the current pace, this cycle is projected to finish at{" "}
                    <span className={healthStyles.text}>{formatNumber(metrics.projected)} entities</span>.
                    Keep daily usage at or below {metrics.dailyBudget.toFixed(1)} for the rest of the cycle.
                  </p>
                  <div className="mt-5 flex items-center gap-2 border-t border-border pt-4 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                    <TimerReset className="size-3.5" />
                    Resets {metrics.reset.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}
                  </div>
                </div>
              </section>
            )}

            <section>
              <div className="mb-3 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Zap className="size-4 text-primary" />
                  <h2 className="font-mono text-[11px] uppercase tracking-widest text-foreground">Rate limit windows</h2>
                </div>
                <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">Current interval</span>
              </div>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {usage.intervals.map((interval) => (
                  <article key={interval.key} className="rounded-lg border border-border bg-card p-4">
                    <div className="mb-5 flex items-center justify-between">
                      <h3 className="font-mono text-xs font-semibold uppercase tracking-wider text-foreground">{interval.label}</h3>
                      <Activity className="size-3.5 text-muted-foreground" />
                    </div>
                    <div className="space-y-5">
                      <div>
                        <p className="mb-2 font-mono text-[9px] uppercase tracking-widest text-muted-foreground">Requests</p>
                        <LimitBar current={interval.requests.current} max={interval.requests.max} />
                      </div>
                      <div>
                        <p className="mb-2 font-mono text-[9px] uppercase tracking-widest text-muted-foreground">Entities</p>
                        <LimitBar current={interval.entities.current} max={interval.entities.max} />
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </>
        )}

        <footer className="mt-8 border-t border-border pt-3 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
          Projections use current-cycle average pace · provider credentials stay server-side
        </footer>
      </div>
    </main>
  );
}
