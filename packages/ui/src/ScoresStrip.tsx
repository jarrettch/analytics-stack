"use client"

import { useEffect, useState } from "react"
import { STRIP_SPORTS, toStripGames, type StripGame } from "./lib/scoresStrip"
import { SPORT_LABELS, type MatchupData, type Sport } from "./types/sports-types"

const POLL_INTERVAL_MS = 30_000

async function fetchSlate(apiBaseUrl: string, sport: Sport, signal: AbortSignal): Promise<MatchupData[]> {
  const endpoint = sport === "nfl" ? "nfl/week" : `${sport}/today`
  const response = await fetch(`${apiBaseUrl}/api/${endpoint}`, { signal })
  if (!response.ok) throw new Error(`${sport} feed returned ${response.status}`)
  const data: unknown = await response.json()
  if (!Array.isArray(data)) throw new Error(`${sport} feed returned a non-array payload`)
  return data as MatchupData[]
}

function TeamName({ team }: { team: StripGame["away"] }) {
  return (
    <span className="font-semibold">
      {team.rank != null && <span className="mr-1 font-normal text-muted-foreground">{team.rank}</span>}
      {team.abbreviation}
    </span>
  )
}

function TeamRow({ team, leading, dim }: { team: StripGame["away"]; leading: boolean; dim: boolean }) {
  const tone = leading ? "text-emerald-400" : dim ? "text-muted-foreground" : "text-foreground"
  return (
    <div className={`flex items-center justify-between gap-3 ${tone}`}>
      <TeamName team={team} />
      <span className="tabular-nums">{team.score}</span>
    </div>
  )
}

export function StripGameCell({ game }: { game: StripGame }) {
  const started = game.state !== "upcoming"
  const body = (
    <>
      <div className={`mb-0.5 flex items-center gap-1 text-[9px] uppercase tracking-wider ${game.state === "live" ? "text-emerald-400" : "text-muted-foreground"}`}>
        {game.state === "live" && <span className="size-1 animate-pulse rounded-full bg-emerald-400" />}
        {game.label}
      </div>
      {started ? (
        <>
          <TeamRow team={game.away} leading={game.away.score > game.home.score} dim={game.state === "final"} />
          <TeamRow team={game.home} leading={game.home.score > game.away.score} dim={game.state === "final"} />
        </>
      ) : (
        <>
          <div className="text-foreground"><TeamName team={game.away} /></div>
          <div className="text-foreground"><TeamName team={game.home} /></div>
        </>
      )}
    </>
  )
  const className = "block w-24 shrink-0 border-r border-border px-2 py-1.5 font-mono text-[11px] leading-tight"
  return game.href ? (
    <a href={game.href} className={`${className} transition-colors hover:bg-secondary`}>{body}</a>
  ) : (
    <div className={className}>{body}</div>
  )
}

/** Compact scoreboard of every sport's current slate, shown across the top of each page. */
export function ScoresStrip({ apiBaseUrl = "" }: { apiBaseUrl?: string }) {
  const [slates, setSlates] = useState<Partial<Record<Sport, StripGame[]>>>({})

  useEffect(() => {
    const controller = new AbortController()

    const refresh = async () => {
      // A sport whose feed fails keeps its last scores rather than vanishing.
      const settled = await Promise.allSettled(
        STRIP_SPORTS.map(async (sport) => [sport, toStripGames(sport, await fetchSlate(apiBaseUrl, sport, controller.signal))] as const),
      )
      if (controller.signal.aborted) return
      const fresh = settled.flatMap((result) => (result.status === "fulfilled" ? [result.value] : []))
      if (fresh.length > 0) setSlates((previous) => ({ ...previous, ...Object.fromEntries(fresh) }))
    }

    refresh()
    const timer = setInterval(() => {
      if (!document.hidden) refresh()
    }, POLL_INTERVAL_MS)

    return () => {
      controller.abort()
      clearInterval(timer)
    }
  }, [apiBaseUrl])

  const sports = STRIP_SPORTS.filter((sport) => (slates[sport]?.length ?? 0) > 0)
  if (sports.length === 0) return null

  return (
    <nav aria-label="Scores" className="flex overflow-x-auto border-b border-border bg-card [scrollbar-width:none]">
      {sports.map((sport) => (
        <div key={sport} className="flex shrink-0">
          <div className="flex shrink-0 items-center border-r border-border bg-secondary px-2 font-mono text-[10px] font-bold uppercase tracking-widest text-primary">
            {SPORT_LABELS[sport]}
          </div>
          {slates[sport]!.map((game) => (
            <StripGameCell key={game.key} game={game} />
          ))}
        </div>
      ))}
    </nav>
  )
}
