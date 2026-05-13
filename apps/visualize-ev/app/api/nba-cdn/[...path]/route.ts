import { NextRequest, NextResponse } from "next/server"

const NBA_LIVE_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  Referer: "https://www.nba.com/",
  Origin: "https://www.nba.com",
  Accept: "*/*",
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params
  const upstream = `https://cdn.nba.com/static/json/liveData/${path.join("/")}`

  try {
    const res = await fetch(upstream, {
      headers: NBA_LIVE_HEADERS,
      signal: AbortSignal.timeout(10_000),
    })
    if (!res.ok) {
      return NextResponse.json(
        { error: `Upstream ${res.status}` },
        { status: res.status },
      )
    }
    return new NextResponse(res.body, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("content-type") ?? "application/json",
        "Cache-Control": "public, max-age=15, stale-while-revalidate=30",
      },
    })
  } catch (err) {
    const name = err instanceof Error ? err.name : "Error"
    return NextResponse.json(
      { error: name === "TimeoutError" ? "Upstream timeout" : "Upstream fetch failed" },
      { status: name === "TimeoutError" ? 504 : 502 },
    )
  }
}
