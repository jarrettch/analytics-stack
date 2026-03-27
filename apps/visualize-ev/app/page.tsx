"use client";

import { useEffect, useState, useRef } from "react";
import { SportsDashboard, type Sport, type MatchupData, SPORT_LABELS, SPORT_MODELS } from "@repo/ui";
import { Activity } from "lucide-react";

const SPORTS: Sport[] = ["nba", "mlb"];

export default function Home() {
  const [activeSport, setActiveSport] = useState<Sport>("nba");
  const [matchups, setMatchups] = useState<MatchupData[]>([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState<Date | null>(null);
  const lastDataRef = useRef<string>("");

  useEffect(() => {
    setNow(new Date());
  }, []);

  useEffect(() => {
    const fetchData = async (isInitial = false) => {
      if (activeSport === "nba" || activeSport === "mlb") {
        if (isInitial) setLoading(true);

        const endpoint = activeSport === "nba" ? "nba/today" : "mlb/today";
        try {
          const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/${endpoint}`);
          const data = await res.json();

          // Only update if data actually changed (prevents unnecessary re-renders)
          const dataStr = JSON.stringify(data);

          if (dataStr !== lastDataRef.current) {
            console.log("Data updated");
            lastDataRef.current = dataStr;
            setMatchups(data);
          }

          setLoading(false);
        } catch (err) {
          console.error(`Failed to fetch ${activeSport.toUpperCase()} data:`, err);
          setLoading(false);
        }
      } else {
        // NFL/NHL coming soon
        setMatchups([]);
        setLoading(false);
      }
    };

    // Initial fetch
    fetchData(true);

    // Set up polling for live games (NBA only — MLB updates slowly)
    let pollInterval: NodeJS.Timeout | null = null;

    if (activeSport === "nba") {
      // Poll every 15 seconds when games might be live
      pollInterval = setInterval(() => fetchData(false), 15000);
    }

    // Cleanup
    return () => {
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [activeSport]);

  const gameCount = matchups.length;
  const hasLiveGames = matchups.some(m => 'gameStatus' in m && (m as any).gameStatus === 2);

  return (
    <main className="min-h-screen bg-background px-4 py-6">
      {/* Dashboard header */}
      <div className="mx-auto mb-4 max-w-6xl">
        <div className="flex items-center justify-between">
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
          <span className="font-mono text-[11px] text-muted-foreground">
            {now?.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toUpperCase()} &mdash; MULTI-SPORT
          </span>
        </div>
        <div className="mt-2 h-px w-full bg-border" />
      </div>

      {/* Sport tabs */}
      <div className="mx-auto max-w-6xl">
        <div className="w-full">
          <div className="flex items-center justify-between">
            {/* Tabs */}
            <div className="flex h-8 bg-secondary rounded-md">
              {SPORTS.map((sport) => (
                <button
                  key={sport}
                  onClick={() => setActiveSport(sport)}
                  className={`px-4 font-mono text-xs uppercase tracking-wider transition-colors ${
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

          {/* Content */}
          <div className="mt-4">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-20 font-mono text-xs text-muted-foreground">
                <div className="mb-2 h-4 w-4 animate-spin border-2 border-primary border-t-transparent rounded-full" />
                INITIALIZING DATA FEED...
              </div>
            ) : matchups.length > 0 ? (
              <SportsDashboard matchups={matchups} />
            ) : (
              <div className="flex flex-col items-center justify-center py-20 font-mono text-xs text-muted-foreground">
                <span className="uppercase">No {SPORT_LABELS[activeSport]} games scheduled today</span>
                {activeSport !== "nba" && activeSport !== "mlb" && (
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
      <div className="mx-auto mt-6 max-w-6xl">
        <div className="h-px w-full bg-border" />
        <div className="flex items-center justify-between pt-2">
          <span className="font-mono text-[10px] text-muted-foreground">
            Models: EV-MODEL-V1 (NBA) &bull; DVOA v3.1 (NFL) &bull; PECOTA v2.8 (MLB) &bull; xG v5.0 (NHL)
          </span>
          <span className="font-mono text-[10px] text-muted-foreground">
            Updated {now?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ET &bull; For entertainment only
          </span>
        </div>
      </div>
    </main>
  );
}