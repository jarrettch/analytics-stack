"use client";

import { useEffect, useState } from "react";
import { NbaDashboard } from "@repo/ui";


export default function Home() {
  const [matchups, setMatchups] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  

  useEffect(() => {
    fetch("http://localhost:8000/api/nba/today")
      .then((res) => res.json())
      .then((data) => {
        console.log("Matchups:", data);
        setMatchups(data);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to fetch NBA data:", err);
        setLoading(false);
      });
  }, []);

  return (
    <main className="min-h-screen bg-background px-4 py-6">
      {/* Dashboard header */}
      <div className="mx-auto mb-4 max-w-5xl">
        <div className="flex items-baseline gap-3">
          <h1 className="font-sans text-lg font-bold text-foreground">
            NBA Matchup Board
          </h1>
          <span className="font-mono text-[10px] uppercase tracking-widest text-primary animate-pulse">
            Live
          </span>
          <span className="ml-auto font-mono text-[11px] text-muted-foreground uppercase">
            {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} &mdash; {matchups.length} GAMES
          </span>
        </div>
        <div className="mt-1 h-px w-full bg-border" />
      </div>

      {/* Grid Container */}
      <div className="mx-auto max-w-5xl">
        {loading ? (
          <div className="flex flex-col items-center justify-center py-20 font-mono text-xs text-muted-foreground">
            <div className="mb-2 h-4 w-4 animate-spin border-2 border-primary border-t-transparent rounded-full" />
            INITIALIZING DATA FEED...
          </div>
        ) : (
          <NbaDashboard matchups={matchups} />
        )}
      </div>

      {/* Simple Footer */}
      <footer className="mx-auto mt-8 max-w-5xl border-t border-border pt-4">
        <div className="flex items-center justify-between">
          <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
            Model: EV-MODEL-V1 &bull; Updated {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} ET
          </span>
          <div className="flex gap-4">
             <span className="font-mono text-[10px] text-muted-foreground uppercase">
               v1.0.4-stable
             </span>
          </div>
        </div>
      </footer>
    </main>
  );
}