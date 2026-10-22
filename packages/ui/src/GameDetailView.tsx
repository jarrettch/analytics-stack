"use client"

import { useEffect, useMemo, useState } from "react"
import { Activity, ArrowLeft, BarChart3, ListOrdered, Star, Table2, Target } from "lucide-react"
import { Badge } from "./components/ui/badge"
import { Separator } from "./components/ui/separator"
import type {
  GameDetail,
  MLBGameDetail,
  MLBTeamBoxScore,
  NBAGameDetail,
  NBATeamBoxScore,
  NFLGameDetail,
  NFLTeamBoxScore,
  NHLGameDetail,
  NHLTeamBoxScore,
  PlayByPlayEvent,
  WinProbPoint,
} from "./types/game-detail-types"
import { startGameDetailPolling } from "./lib/gameDetailPolling"

function fmt(v: number | null | undefined, digits = 1): string {
  return v == null || Number.isNaN(v) ? "--" : v.toFixed(digits)
}

function statusLabel(detail: GameDetail): string {
  if (detail.status === "live") return detail.matchup.status || "Live"
  if (detail.status === "final") return detail.rawStatus || "Final"
  return detail.matchup.time || detail.matchup.status || "Preview"
}

function TeamLogo({
  abbreviation,
  espnSlug,
  sport,
}: {
  abbreviation: string
  espnSlug?: string
  sport: string
}) {
  const slug = espnSlug ?? (abbreviation === "AZ" ? "ari" : abbreviation.toLowerCase())
  return (
    <img
      src={`https://a.espncdn.com/i/teamlogos/${sport}/500/${slug}.png`}
      alt={`${abbreviation} logo`}
      className="size-10"
      onError={(e) => (e.currentTarget.style.display = "none")}
    />
  )
}

function Header({ detail }: { detail: GameDetail }) {
  const { matchup } = detail
  const awayTotal = detail.sport === "mlb" ? detail.awayScore.runs : detail.awayScore.total
  const homeTotal = detail.sport === "mlb" ? detail.homeScore.runs : detail.homeScore.total
  const showScore = detail.status !== "preview"

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border bg-secondary px-3 py-2">
        <a href="/" className="flex items-center gap-2 text-muted-foreground transition-colors hover:text-foreground">
          <ArrowLeft className="size-4" />
          <span className="font-mono text-xs uppercase tracking-wider">All Games</span>
        </a>
        <div className="flex items-center gap-2">
          <Activity className="size-3 text-primary" />
          <span className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            {matchup.model}
          </span>
        </div>
      </div>

      <div className="grid gap-4 px-4 py-4 md:grid-cols-[1fr_auto_1fr] md:items-center">
        <div className="flex items-center gap-3 md:justify-end">
          <div className="text-right">
            <div className="font-sans text-lg font-bold text-foreground">{matchup.away.name}</div>
            <div className="font-mono text-[10px] text-muted-foreground">
              {matchup.away.abbreviation} · {matchup.away.record}
            </div>
          </div>
          <TeamLogo abbreviation={matchup.away.abbreviation} espnSlug={matchup.away.espnSlug} sport={detail.sport} />
          <div className={`w-14 text-center font-mono text-4xl font-bold tabular-nums ${awayTotal > homeTotal ? "text-emerald-400" : "text-foreground"}`}>
            {showScore ? awayTotal : "-"}
          </div>
        </div>

        <div className="flex flex-col items-center gap-1">
          <Badge
            variant="outline"
            className={`border-border bg-transparent px-2 py-0 font-mono text-[10px] uppercase ${
              detail.status === "live" ? "text-emerald-400" : "text-muted-foreground"
            }`}
          >
            {statusLabel(detail)}
          </Badge>
          <span className="font-mono text-[10px] text-muted-foreground">{matchup.date}</span>
          <span className="max-w-64 truncate text-center font-mono text-[10px] text-muted-foreground">
            {matchup.venue}
          </span>
          <div className="mt-1 flex flex-wrap justify-center gap-2 font-mono text-[10px] text-muted-foreground">
            <span>{matchup.spreadLine}</span>
            <span>O/U {matchup.overUnder == null ? "N/A" : matchup.overUnder.toFixed(1)}</span>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className={`w-14 text-center font-mono text-4xl font-bold tabular-nums ${homeTotal > awayTotal ? "text-emerald-400" : "text-foreground"}`}>
            {showScore ? homeTotal : "-"}
          </div>
          <TeamLogo abbreviation={matchup.home.abbreviation} espnSlug={matchup.home.espnSlug} sport={detail.sport} />
          <div>
            <div className="font-sans text-lg font-bold text-foreground">{matchup.home.name}</div>
            <div className="font-mono text-[10px] text-muted-foreground">
              {matchup.home.abbreviation} · {matchup.home.record}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function Linescore({ detail }: { detail: GameDetail }) {
  const away = detail.matchup.away.abbreviation
  const home = detail.matchup.home.abbreviation
  if (detail.sport === "mlb") {
    const inningCount = Math.max(detail.awayScore.innings.length, detail.homeScore.innings.length, 9)
    const columns = [
      ...Array.from({ length: inningCount }, (_, i) => String(i + 1)),
      "R",
      "H",
      "E",
    ]
    const awayVals = [...detail.awayScore.innings, detail.awayScore.runs, detail.awayScore.hits, detail.awayScore.errors]
    const homeVals = [...detail.homeScore.innings, detail.homeScore.runs, detail.homeScore.hits, detail.homeScore.errors]

    return (
      <div className="overflow-x-auto rounded-md border border-border bg-card">
        <table className="w-full min-w-[640px] text-left font-mono text-xs">
          <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Team</th>
              {columns.map((c) => (
                <th key={c} className="px-3 py-2 text-right">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[{ team: away, vals: awayVals }, { team: home, vals: homeVals }].map((row) => (
              <tr key={row.team} className="border-t border-border">
                <td className="px-3 py-2 font-semibold text-foreground">{row.team}</td>
                {row.vals.map((v, i) => (
                  <td key={i} className={`px-3 py-2 text-right tabular-nums ${i >= row.vals.length - 3 ? "font-bold text-primary" : "text-foreground"}`}>
                    {v ?? "-"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  let columns: string[]
  let awayVals: number[]
  let homeVals: number[]
  if (detail.sport === "nba" || detail.sport === "nfl") {
    columns = ["Q1", "Q2", "Q3", "Q4", ...((detail.awayScore.ot ?? []).map((_, i) => `OT${i + 1}`)), "T"]
    awayVals = [detail.awayScore.q1, detail.awayScore.q2, detail.awayScore.q3, detail.awayScore.q4, ...(detail.awayScore.ot ?? []), detail.awayScore.total]
    homeVals = [detail.homeScore.q1, detail.homeScore.q2, detail.homeScore.q3, detail.homeScore.q4, ...(detail.homeScore.ot ?? []), detail.homeScore.total]
  } else {
    columns = ["1st", "2nd", "3rd", ...(detail.awayScore.ot !== undefined ? ["OT"] : []), ...(detail.awayScore.so !== undefined ? ["SO"] : []), "T"]
    awayVals = [detail.awayScore.p1, detail.awayScore.p2, detail.awayScore.p3, ...(detail.awayScore.ot !== undefined ? [detail.awayScore.ot] : []), ...(detail.awayScore.so !== undefined ? [detail.awayScore.so] : []), detail.awayScore.total]
    homeVals = [detail.homeScore.p1, detail.homeScore.p2, detail.homeScore.p3, ...(detail.homeScore.ot !== undefined ? [detail.homeScore.ot] : []), ...(detail.homeScore.so !== undefined ? [detail.homeScore.so] : []), detail.homeScore.total]
  }

  return (
    <div className="overflow-x-auto rounded-md border border-border bg-card">
      <table className="w-full min-w-[420px] text-left font-mono text-xs">
        <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Team</th>
            {columns.map((c) => (
              <th key={c} className="px-3 py-2 text-right">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[{ team: away, vals: awayVals }, { team: home, vals: homeVals }].map((row) => (
            <tr key={row.team} className="border-t border-border">
              <td className="px-3 py-2 font-semibold text-foreground">{row.team}</td>
              {row.vals.map((v, i) => (
                <td key={i} className={`px-3 py-2 text-right tabular-nums ${i === row.vals.length - 1 ? "font-bold text-primary" : "text-foreground"}`}>
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function WinProbabilityChart({ data, awayAbbr, homeAbbr }: { data: WinProbPoint[]; awayAbbr: string; homeAbbr: string }) {
  if (data.length === 0) {
    return <EmptyPanel label="Win probability is not available for this game yet." />
  }
  const latest = data[data.length - 1]!
  const points = data
    .map((p) => `${p.x.toFixed(1)},${(100 - p.awayProb).toFixed(1)}`)
    .join(" ")

  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="font-mono text-xs font-semibold text-emerald-400">{awayAbbr} {latest.awayProb.toFixed(1)}%</div>
        <div className="font-mono text-xs font-semibold text-red-400">{homeAbbr} {latest.homeProb.toFixed(1)}%</div>
      </div>
      <div className="mb-3 flex h-2.5 overflow-hidden rounded-sm">
        <div className="bg-emerald-500" style={{ width: `${latest.awayProb}%` }} />
        <div className="bg-red-500/80" style={{ width: `${latest.homeProb}%` }} />
      </div>
      <div className="relative h-28 rounded border border-border bg-background">
        <div className="absolute inset-x-0 top-1/2 h-px bg-border" />
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
          <polyline points={points} fill="none" stroke="rgb(16 185 129)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
    </div>
  )
}

function EmptyPanel({ label }: { label: string }) {
  return (
    <div className="rounded-md border border-border bg-card px-3 py-10 text-center font-mono text-xs text-muted-foreground">
      {label}
    </div>
  )
}

function PlaysTable({ plays }: { plays: PlayByPlayEvent[] }) {
  if (plays.length === 0) return <EmptyPanel label="Play-by-play is not available yet." />
  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <table className="w-full text-left font-mono text-xs">
        <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Time</th>
            <th className="px-3 py-2">Team</th>
            <th className="px-3 py-2">Event</th>
            <th className="px-3 py-2 text-right">Score</th>
          </tr>
        </thead>
        <tbody>
          {plays.slice(-80).reverse().map((p) => (
            <tr key={p.id} className="border-t border-border">
              <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">{p.quarter} {p.clock}</td>
              <td className="px-3 py-2 text-foreground">{p.team ?? "--"}</td>
              <td className="px-3 py-2 text-foreground">{p.event}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-muted-foreground">
                {p.awayScore}-{p.homeScore}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const NBA_BOX_COLUMNS = ["Player", "Min", "Pts", "Reb", "Ast", "Stl", "Blk", "TO", "FG", "3P", "FT", "+/-"]

export function NBABoxScoreTable({ box, team }: { box: NBATeamBoxScore | null; team: string }) {
  if (!box || box.players.length === 0) return <EmptyPanel label={`${team} box score is not available yet.`} />
  return (
    <div className="overflow-x-auto rounded-md border border-border bg-card">
      <table className="w-full min-w-[760px] text-left font-mono text-xs">
        <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
          <tr>
            {NBA_BOX_COLUMNS.map((h) => (
              <th key={h} className={`px-3 py-2 ${h !== "Player" ? "text-right" : ""}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {box.players.map((p) => p.dnp ? (
            <tr key={`${team}-${p.name}`} className="border-t border-border">
              <td className="px-3 py-2 text-foreground">{p.name} <span className="text-muted-foreground">{p.position}</span></td>
              <td colSpan={NBA_BOX_COLUMNS.length - 1} className="px-3 py-2 text-muted-foreground">DNP - {p.dnp}</td>
            </tr>
          ) : (
            <tr key={`${team}-${p.name}`} className="border-t border-border">
              <td className="px-3 py-2 text-foreground">{p.name} <span className="text-muted-foreground">{p.position}</span></td>
              <td className="px-3 py-2 text-right">{p.minutes}</td>
              <td className="px-3 py-2 text-right font-semibold text-primary">{p.points}</td>
              <td className="px-3 py-2 text-right">{p.rebounds}</td>
              <td className="px-3 py-2 text-right">{p.assists}</td>
              <td className="px-3 py-2 text-right">{p.steals}</td>
              <td className="px-3 py-2 text-right">{p.blocks}</td>
              <td className="px-3 py-2 text-right">{p.turnovers}</td>
              <td className="px-3 py-2 text-right">{p.fgm}-{p.fga}</td>
              <td className="px-3 py-2 text-right">{p.threePm}-{p.threePa}</td>
              <td className="px-3 py-2 text-right">{p.ftm}-{p.fta}</td>
              <td className="px-3 py-2 text-right">{p.plusMinus > 0 ? "+" : ""}{p.plusMinus}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function NFLPlayerTables({ box, team }: { box: NFLTeamBoxScore | null; team: string }) {
  if (!box) return <EmptyPanel label={`${team} box score is not available yet.`} />
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-border bg-card p-3">
        <div className="mb-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{team} team stats</div>
        <div className="grid grid-cols-2 gap-x-6 md:grid-cols-3">
          <Metric label="Total Yards" value={String(box.teamStats.totalYards)} />
          <Metric label="First Downs" value={String(box.teamStats.firstDowns)} />
          <Metric label="Turnovers" value={String(box.teamStats.turnovers)} />
          <Metric label="3rd Down" value={box.teamStats.thirdDownEff} />
          <Metric label="Red Zone" value={box.teamStats.redZoneEff} />
          <Metric label="Possession" value={box.teamStats.possession} />
        </div>
      </div>

      {box.passing.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border bg-card">
          <table className="w-full min-w-[620px] text-left font-mono text-xs">
            <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
              <tr>{["Passer", "C/ATT", "Yds", "TD", "INT", "Rating", "Sacks"].map((h) => <th key={h} className={`px-3 py-2 ${h !== "Passer" ? "text-right" : ""}`}>{h}</th>)}</tr>
            </thead>
            <tbody>{box.passing.map((p) => (
              <tr key={`${team}-pass-${p.name}`} className="border-t border-border">
                <td className="px-3 py-2 text-foreground">{p.name}</td>
                <td className="px-3 py-2 text-right">{p.completions}/{p.attempts}</td>
                <td className="px-3 py-2 text-right font-semibold text-primary">{p.yards}</td>
                <td className="px-3 py-2 text-right">{p.touchdowns}</td>
                <td className="px-3 py-2 text-right">{p.interceptions}</td>
                <td className="px-3 py-2 text-right">{p.rating.toFixed(1)}</td>
                <td className="px-3 py-2 text-right">{p.sacks}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {box.rushing.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border bg-card">
          <table className="w-full min-w-[520px] text-left font-mono text-xs">
            <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
              <tr>{["Rusher", "Car", "Yds", "Avg", "TD", "Long"].map((h) => <th key={h} className={`px-3 py-2 ${h !== "Rusher" ? "text-right" : ""}`}>{h}</th>)}</tr>
            </thead>
            <tbody>{box.rushing.map((p) => (
              <tr key={`${team}-rush-${p.name}`} className="border-t border-border">
                <td className="px-3 py-2 text-foreground">{p.name}</td>
                <td className="px-3 py-2 text-right">{p.carries}</td>
                <td className="px-3 py-2 text-right font-semibold text-primary">{p.yards}</td>
                <td className="px-3 py-2 text-right">{p.average.toFixed(1)}</td>
                <td className="px-3 py-2 text-right">{p.touchdowns}</td>
                <td className="px-3 py-2 text-right">{p.longest}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {box.receiving.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border bg-card">
          <table className="w-full min-w-[600px] text-left font-mono text-xs">
            <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
              <tr>{["Receiver", "Rec", "Tgt", "Yds", "Avg", "TD", "Long"].map((h) => <th key={h} className={`px-3 py-2 ${h !== "Receiver" ? "text-right" : ""}`}>{h}</th>)}</tr>
            </thead>
            <tbody>{box.receiving.map((p) => (
              <tr key={`${team}-rec-${p.name}`} className="border-t border-border">
                <td className="px-3 py-2 text-foreground">{p.name}</td>
                <td className="px-3 py-2 text-right">{p.receptions}</td>
                <td className="px-3 py-2 text-right">{p.targets}</td>
                <td className="px-3 py-2 text-right font-semibold text-primary">{p.yards}</td>
                <td className="px-3 py-2 text-right">{p.average.toFixed(1)}</td>
                <td className="px-3 py-2 text-right">{p.touchdowns}</td>
                <td className="px-3 py-2 text-right">{p.longest}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function NHLBoxScoreTable({ box, team }: { box: NHLTeamBoxScore | null; team: string }) {
  if (!box || (box.skaters.length === 0 && box.goalies.length === 0)) {
    return <EmptyPanel label={`${team} box score is not available yet.`} />
  }
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-md border border-border bg-card">
        <table className="w-full min-w-[720px] text-left font-mono text-xs">
          <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
            <tr>
              {["Skater", "G", "A", "Pts", "+/-", "PIM", "SOG", "Hit", "Blk", "TOI"].map((h) => (
                <th key={h} className={`px-3 py-2 ${h !== "Skater" ? "text-right" : ""}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {box.skaters.map((p) => (
              <tr key={`${team}-${p.name}`} className="border-t border-border">
                <td className="px-3 py-2 text-foreground">{p.name} <span className="text-muted-foreground">{p.position}</span></td>
                <td className="px-3 py-2 text-right">{p.goals}</td>
                <td className="px-3 py-2 text-right">{p.assists}</td>
                <td className="px-3 py-2 text-right font-semibold text-primary">{p.points}</td>
                <td className="px-3 py-2 text-right">{p.plusMinus > 0 ? "+" : ""}{p.plusMinus}</td>
                <td className="px-3 py-2 text-right">{p.pim}</td>
                <td className="px-3 py-2 text-right">{p.shots}</td>
                <td className="px-3 py-2 text-right">{p.hits}</td>
                <td className="px-3 py-2 text-right">{p.blocks}</td>
                <td className="px-3 py-2 text-right">{p.toi}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {box.goalies.length > 0 && (
        <div className="grid gap-2 md:grid-cols-2">
          {box.goalies.map((g) => (
            <div key={`${team}-${g.name}`} className="rounded-md border border-border bg-card px-3 py-2">
              <div className="font-mono text-xs font-semibold text-foreground">{g.name}</div>
              <div className="mt-1 grid grid-cols-4 gap-2 font-mono text-[10px] text-muted-foreground">
                <span>SV {g.saves}</span>
                <span>SA {g.shotsAgainst}</span>
                <span>SV% {g.savePct}</span>
                <span>TOI {g.toi}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function MLBBoxScoreTable({ box, team }: { box: MLBTeamBoxScore | null; team: string }) {
  if (!box || (box.hitting.length === 0 && box.pitching.length === 0)) {
    return <EmptyPanel label={`${team} box score is not available yet.`} />
  }

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-md border border-border bg-card">
        <table className="w-full min-w-[760px] text-left font-mono text-xs">
          <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
            <tr>
              {["Hitter", "AB", "R", "H", "RBI", "HR", "BB", "SO", "AVG", "OBP", "SLG"].map((h) => (
                <th key={h} className={`px-3 py-2 ${h !== "Hitter" ? "text-right" : ""}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {box.hitting.map((p) => (
              <tr key={`${team}-${p.name}-hit`} className="border-t border-border">
                <td className="px-3 py-2 text-foreground">{p.name} <span className="text-muted-foreground">{p.position}</span></td>
                <td className="px-3 py-2 text-right">{p.ab}</td>
                <td className="px-3 py-2 text-right">{p.runs}</td>
                <td className="px-3 py-2 text-right font-semibold text-primary">{p.hits}</td>
                <td className="px-3 py-2 text-right">{p.rbi}</td>
                <td className="px-3 py-2 text-right">{p.hr}</td>
                <td className="px-3 py-2 text-right">{p.bb}</td>
                <td className="px-3 py-2 text-right">{p.so}</td>
                <td className="px-3 py-2 text-right">{p.avg}</td>
                <td className="px-3 py-2 text-right">{p.obp}</td>
                <td className="px-3 py-2 text-right">{p.slg}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto rounded-md border border-border bg-card">
        <table className="w-full min-w-[620px] text-left font-mono text-xs">
          <thead className="bg-secondary text-[10px] uppercase tracking-wider text-muted-foreground">
            <tr>
              {["Pitcher", "IP", "H", "R", "ER", "BB", "SO", "HR", "Pit", "Str", "ERA"].map((h) => (
                <th key={h} className={`px-3 py-2 ${h !== "Pitcher" ? "text-right" : ""}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {box.pitching.map((p) => (
              <tr key={`${team}-${p.name}-pitch`} className="border-t border-border">
                <td className="px-3 py-2 text-foreground">{p.name}</td>
                <td className="px-3 py-2 text-right font-semibold text-primary">{p.ip}</td>
                <td className="px-3 py-2 text-right">{p.hits}</td>
                <td className="px-3 py-2 text-right">{p.runs}</td>
                <td className="px-3 py-2 text-right">{p.earnedRuns}</td>
                <td className="px-3 py-2 text-right">{p.bb}</td>
                <td className="px-3 py-2 text-right">{p.so}</td>
                <td className="px-3 py-2 text-right">{p.hr}</td>
                <td className="px-3 py-2 text-right">{p.pitchCount}</td>
                <td className="px-3 py-2 text-right">{p.strikes}</td>
                <td className="px-3 py-2 text-right">{p.era}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function NBAContent({ detail }: { detail: NBAGameDetail }) {
  const [tab, setTab] = useState("winprob")
  const tabs = [
    ["winprob", "Win Prob", BarChart3],
    ["shooting", "Shooting", Target],
    ["matchup", "Matchup", Activity],
    ["boxscore", "Boxscore", Table2],
  ] as const
  return (
    <TabbedShell tabs={tabs} active={tab} setActive={setTab}>
      {tab === "winprob" && (
        <div className="space-y-3">
          <WinProbabilityChart data={detail.winProbability} awayAbbr={detail.matchup.away.abbreviation} homeAbbr={detail.matchup.home.abbreviation} />
          <PlaysTable plays={detail.playByPlay} />
        </div>
      )}
      {tab === "shooting" && (
        detail.scoringEfficiency ? (
          <div className="grid gap-3 md:grid-cols-2">
            {[
              ["Away", detail.matchup.away.abbreviation, detail.scoringEfficiency.away],
              ["Home", detail.matchup.home.abbreviation, detail.scoringEfficiency.home],
            ].map(([side, abbr, eff]) => {
              const efficiency = eff as NonNullable<NBAGameDetail["scoringEfficiency"]>["away"]
              return (
              <div key={side as string} className="rounded-md border border-border bg-card p-3">
                <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{abbr as string} efficiency</div>
                <div className="mt-3 grid grid-cols-3 gap-3">
                  <Metric label="PPP" value={fmt(efficiency.ppp, 3)} />
                  <Metric label="TS%" value={fmt(efficiency.tsPct, 1)} />
                  <Metric label="Poss" value={fmt(efficiency.possessions, 1)} />
                </div>
              </div>
            )})}
          </div>
        ) : <EmptyPanel label="Shooting efficiency is not available yet." />
      )}
      {tab === "matchup" && (
        <div className="rounded-md border border-border bg-card p-3">
          <Comparison label="ORtg" away={detail.matchup.away.offRating} home={detail.matchup.home.offRating} />
          <Comparison label="DRtg" away={detail.matchup.away.defRating} home={detail.matchup.home.defRating} lower />
          <Comparison label="Pace" away={detail.matchup.away.pace} home={detail.matchup.home.pace} />
          <Comparison label="Win%" away={detail.matchup.away.winPct * 100} home={detail.matchup.home.winPct * 100} />
        </div>
      )}
      {tab === "boxscore" && (
        <div className="space-y-4">
          <NBABoxScoreTable box={detail.awayBoxScore} team={detail.matchup.away.abbreviation} />
          <NBABoxScoreTable box={detail.homeBoxScore} team={detail.matchup.home.abbreviation} />
        </div>
      )}
    </TabbedShell>
  )
}

function NFLContent({ detail }: { detail: NFLGameDetail }) {
  const [tab, setTab] = useState("matchup")
  const tabs = [
    ["winprob", "Win Prob", BarChart3],
    ["matchup", "Matchup", Activity],
    ["boxscore", "Boxscore", Table2],
    ["plays", "Plays", ListOrdered],
  ] as const
  return (
    <TabbedShell tabs={tabs} active={tab} setActive={setTab}>
      {tab === "winprob" && (
        <div className="space-y-3">
          <WinProbabilityChart data={detail.winProbability} awayAbbr={detail.matchup.away.abbreviation} homeAbbr={detail.matchup.home.abbreviation} />
          <PlaysTable plays={detail.playByPlay} />
        </div>
      )}
      {tab === "matchup" && (
        <div className="space-y-3">
          <div className="rounded-md border border-border bg-card p-3">
            {detail.matchup.away.pointsFor != null && detail.matchup.home.pointsFor != null && (
              <Comparison label="PF/G" away={detail.matchup.away.pointsFor} home={detail.matchup.home.pointsFor} />
            )}
            {detail.matchup.away.pointsAgainst != null && detail.matchup.home.pointsAgainst != null && (
              <Comparison label="PA/G" away={detail.matchup.away.pointsAgainst} home={detail.matchup.home.pointsAgainst} lower />
            )}
            <Comparison label="Win%" away={detail.matchup.away.winPct * 100} home={detail.matchup.home.winPct * 100} />
          </div>
          {detail.awayBoxScore && detail.homeBoxScore && (
            <div className="rounded-md border border-border bg-card p-3">
              <Comparison label="Total Yards" away={detail.awayBoxScore.teamStats.totalYards} home={detail.homeBoxScore.teamStats.totalYards} />
              <Comparison label="Passing" away={detail.awayBoxScore.teamStats.passingYards} home={detail.homeBoxScore.teamStats.passingYards} />
              <Comparison label="Rushing" away={detail.awayBoxScore.teamStats.rushingYards} home={detail.homeBoxScore.teamStats.rushingYards} />
              <Comparison label="Turnovers" away={detail.awayBoxScore.teamStats.turnovers} home={detail.homeBoxScore.teamStats.turnovers} lower />
            </div>
          )}
        </div>
      )}
      {tab === "boxscore" && (
        <div className="space-y-4">
          <NFLPlayerTables box={detail.awayBoxScore} team={detail.matchup.away.abbreviation} />
          <NFLPlayerTables box={detail.homeBoxScore} team={detail.matchup.home.abbreviation} />
        </div>
      )}
      {tab === "plays" && <PlaysTable plays={detail.playByPlay} />}
    </TabbedShell>
  )
}

function NHLContent({ detail }: { detail: NHLGameDetail }) {
  const [tab, setTab] = useState("matchup")
  const tabs = [
    ["matchup", "Matchup", Activity],
    ["boxscore", "Boxscore", Table2],
    ["plays", "Plays", ListOrdered],
    ["stars", "Stars", Star],
  ] as const
  return (
    <TabbedShell tabs={tabs} active={tab} setActive={setTab}>
      {tab === "matchup" && (
        detail.awayBoxScore && detail.homeBoxScore ? (
          <div className="rounded-md border border-border bg-card p-3">
            <Comparison label="Shots" away={detail.awayBoxScore.teamStats.shots} home={detail.homeBoxScore.teamStats.shots} />
            <Comparison label="Hits" away={detail.awayBoxScore.teamStats.hits} home={detail.homeBoxScore.teamStats.hits} />
            <Comparison label="Blocks" away={detail.awayBoxScore.teamStats.blocks} home={detail.homeBoxScore.teamStats.blocks} />
            <Comparison label="PIM" away={detail.awayBoxScore.teamStats.pim} home={detail.homeBoxScore.teamStats.pim} lower />
            <Comparison label="FO%" away={faceoffPct(detail.awayBoxScore.teamStats)} home={faceoffPct(detail.homeBoxScore.teamStats)} />
          </div>
        ) : <EmptyPanel label="Team stats are not available yet." />
      )}
      {tab === "boxscore" && (
        <div className="space-y-4">
          <NHLBoxScoreTable box={detail.awayBoxScore} team={detail.matchup.away.abbreviation} />
          <NHLBoxScoreTable box={detail.homeBoxScore} team={detail.matchup.home.abbreviation} />
        </div>
      )}
      {tab === "plays" && <PlaysTable plays={detail.playByPlay} />}
      {tab === "stars" && (
        detail.threeStars?.length ? (
          <div className="grid gap-3 md:grid-cols-3">
            {detail.threeStars.map((s, i) => (
              <div key={`${s.name}-${i}`} className="rounded-md border border-border bg-card p-3">
                <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Star {i + 1}</div>
                <div className="mt-2 font-sans text-sm font-semibold text-foreground">{s.name}</div>
                <div className="font-mono text-[10px] text-muted-foreground">{s.team} {s.position ?? ""}</div>
              </div>
            ))}
          </div>
        ) : <EmptyPanel label="Three Stars are available after final games when NHL publishes them." />
      )}
    </TabbedShell>
  )
}

function MLBContent({ detail }: { detail: MLBGameDetail }) {
  const [tab, setTab] = useState("gamefeed")
  const tabs = [
    ["gamefeed", "Gamefeed", BarChart3],
    ["boxscore", "Boxscore", Table2],
    ["plays", "Plays", ListOrdered],
    ["matchup", "Matchup", Activity],
  ] as const

  return (
    <TabbedShell tabs={tabs} active={tab} setActive={setTab}>
      {tab === "gamefeed" && (
        <div className="space-y-3">
          <WinProbabilityChart data={detail.winProbability} awayAbbr={detail.matchup.away.abbreviation} homeAbbr={detail.matchup.home.abbreviation} />
          <PlaysTable plays={detail.playByPlay} />
        </div>
      )}
      {tab === "boxscore" && (
        <div className="space-y-4">
          <MLBBoxScoreTable box={detail.awayBoxScore} team={detail.matchup.away.abbreviation} />
          <MLBBoxScoreTable box={detail.homeBoxScore} team={detail.matchup.home.abbreviation} />
        </div>
      )}
      {tab === "plays" && <PlaysTable plays={detail.playByPlay} />}
      {tab === "matchup" && (
        <div className="rounded-md border border-border bg-card p-3">
          <Comparison label="RS/G" away={detail.matchup.away.runsScored} home={detail.matchup.home.runsScored} />
          <Comparison label="RA/G" away={detail.matchup.away.runsAllowed} home={detail.matchup.home.runsAllowed} lower />
          <Comparison label="ERA" away={detail.matchup.away.era} home={detail.matchup.home.era} lower />
          <Comparison label="OPS" away={detail.matchup.away.ops} home={detail.matchup.home.ops} />
          <Comparison label="Win%" away={detail.matchup.away.winPct * 100} home={detail.matchup.home.winPct * 100} />
        </div>
      )}
    </TabbedShell>
  )
}

function faceoffPct(stats: { faceoffWins: number; faceoffTotal: number }) {
  return stats.faceoffTotal > 0 ? (stats.faceoffWins / stats.faceoffTotal) * 100 : 0
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="font-mono text-xl font-bold tabular-nums text-foreground">{value}</div>
    </div>
  )
}

function Comparison({ label, away, home, lower = false }: { label: string; away: number; home: number; lower?: boolean }) {
  const awayBetter = lower ? away < home : away > home
  return (
    <div className="grid grid-cols-[1fr_72px_1fr] items-center gap-3 border-b border-border py-2 last:border-b-0">
      <div className={`text-right font-mono text-sm tabular-nums ${awayBetter ? "text-primary" : "text-muted-foreground"}`}>{fmt(away)}</div>
      <div className="text-center font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`font-mono text-sm tabular-nums ${!awayBetter ? "text-primary" : "text-muted-foreground"}`}>{fmt(home)}</div>
    </div>
  )
}

function TabbedShell({
  tabs,
  active,
  setActive,
  children,
}: {
  tabs: readonly (readonly [string, string, React.ElementType])[]
  active: string
  setActive: (value: string) => void
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1 rounded-md bg-secondary p-1">
        {tabs.map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => setActive(id)}
            className={`flex h-8 items-center gap-1.5 rounded-sm px-3 font-mono text-xs uppercase tracking-wider transition-colors ${
              active === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="size-3" />
            {label}
          </button>
        ))}
      </div>
      {children}
    </div>
  )
}

export function GameDetailView({
  detail: initialDetail,
  apiBaseUrl = "",
}: {
  detail: GameDetail
  apiBaseUrl?: string
}) {
  const [detail, setDetail] = useState(initialDetail)

  useEffect(() => {
    setDetail(initialDetail)
  }, [initialDetail])

  useEffect(() => {
    const { gameId, sport, status } = initialDetail

    return startGameDetailPolling<GameDetail>({
      initialStatus: status,
      fetchDetail: async (signal) => {
        const response = await fetch(
          `${apiBaseUrl}/api/${sport}/game/${encodeURIComponent(gameId)}`,
          { cache: "no-store", signal },
        )
        if (!response.ok) {
          throw new Error(`Detail feed returned ${response.status}`)
        }
        return (await response.json()) as GameDetail
      },
      onDetail: setDetail,
      onError: (error) => {
        console.error(`Failed to refresh ${sport.toUpperCase()} game ${gameId}:`, error)
      },
    })
  }, [apiBaseUrl, initialDetail])

  const title = useMemo(() => `${detail.matchup.away.abbreviation} @ ${detail.matchup.home.abbreviation}`, [detail])
  return (
    <main className="min-h-screen bg-background px-4 py-6">
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h1 className="font-sans text-lg font-bold text-foreground">{title}</h1>
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              {detail.sport.toUpperCase()} Game Detail · {detail.gameId}
            </p>
          </div>
          <Badge variant="outline" className="border-border bg-transparent font-mono text-[10px] text-muted-foreground">
            {detail.status}
          </Badge>
        </div>
        <Header detail={detail} />
        <div className="my-4">
          <Linescore detail={detail} />
        </div>
        <Separator className="mb-4 bg-border" />
        {detail.sport === "nba" && <NBAContent detail={detail} />}
        {detail.sport === "nfl" && <NFLContent detail={detail} />}
        {detail.sport === "nhl" && <NHLContent detail={detail} />}
        {detail.sport === "mlb" && <MLBContent detail={detail} />}
      </div>
    </main>
  )
}
