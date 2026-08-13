import { GameDetailView, type GameDetail, type Sport } from "@repo/ui"
import { Activity, ArrowLeft } from "lucide-react"
import Link from "next/link"

type PageProps = {
  params: Promise<{ gameId: string }>
  searchParams: Promise<{ sport?: string }>
}

function isDetailSport(value: string | undefined): value is Extract<Sport, "nba" | "nfl" | "nhl" | "mlb"> {
  return value === "nba" || value === "nfl" || value === "nhl" || value === "mlb"
}

export default async function GamePage({ params, searchParams }: PageProps) {
  const { gameId } = await params
  const { sport } = await searchParams

  if (!isDetailSport(sport)) {
    return <ErrorState message="Game detail currently supports NBA, NFL, NHL, and MLB. Open a game from one of those dashboards." />
  }

  try {
    const apiBase = process.env.NEXT_PUBLIC_API_URL
    const res = await fetch(`${apiBase}/api/${sport}/game/${gameId}`, {
      cache: "no-store",
    })

    if (!res.ok) {
      return <ErrorState message={`Detail feed returned ${res.status} for ${sport.toUpperCase()} game ${gameId}.`} />
    }

    const detail = (await res.json()) as GameDetail
    return <GameDetailView detail={detail} />
  } catch {
    return <ErrorState message={`Could not load ${sport.toUpperCase()} game ${gameId}.`} />
  }
}

function ErrorState({ message }: { message: string }) {
  return (
    <main className="min-h-screen bg-background px-4 py-6">
      <div className="mx-auto max-w-3xl rounded-md border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border bg-secondary px-3 py-2">
          <Link href="/" className="flex items-center gap-2 text-muted-foreground transition-colors hover:text-foreground">
            <ArrowLeft className="size-4" />
            <span className="font-mono text-xs uppercase tracking-wider">All Games</span>
          </Link>
          <Activity className="size-3 text-primary" />
        </div>
        <div className="px-4 py-10 text-center">
          <h1 className="font-sans text-lg font-bold text-foreground">Game detail unavailable</h1>
          <p className="mt-2 font-mono text-xs text-muted-foreground">{message}</p>
        </div>
      </div>
    </main>
  )
}
