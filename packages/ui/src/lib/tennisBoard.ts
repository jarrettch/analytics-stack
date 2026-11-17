// PrizePicks tennis board: browser-side extraction of the rows the API prices, and the
// grouping the page shows them in. See apps/api/specs/tennis_fs_upload.md.

export const TENNIS_STATS = ["Fantasy Score", "Total Games", "Total Games Won"] as const
export type TennisPricedStat = (typeof TENNIS_STATS)[number]
export type TennisOddsType = "standard" | "goblin" | "demon"

// Aces rows are uploaded as model input only; they are never priced.
const UPLOADED_STATS = new Set<string>([...TENNIS_STATS, "Aces"])
const TENNIS_LEAGUE_ID = "5"
export const MAX_BOARD_ROWS = 1000

export interface TennisBoardRow {
  player: string
  opponent: string
  stat: string
  odds_type: string
  line: number
  start_time: string
  status: string
  projection_id: string
  game_id: string
}

export interface TennisBoard {
  rows: TennisBoardRow[]
  counts: Record<TennisPricedStat, number>
  doublesSkipped: number
  notPreGameSkipped: number
}

export type TennisBoardParse = { ok: true; board: TennisBoard } | { ok: false; error: string }

type Json = Record<string, any> // eslint-disable-line @typescript-eslint/no-explicit-any

/** Reduce a PrizePicks projections payload to the tennis rows the models read. */
export function extractTennisBoard(text: string): TennisBoardParse {
  let payload: Json
  try {
    payload = JSON.parse(text)
  } catch {
    return { ok: false, error: "That isn't valid JSON. Copy the whole projections page, from the first { to the last }." }
  }
  if (!payload || !Array.isArray(payload.data) || !Array.isArray(payload.included)) {
    return { ok: false, error: "This doesn't look like a PrizePicks projections payload (no data and included lists)." }
  }
  const players = new Map<string, string>()
  for (const item of payload.included as Json[]) {
    if (item?.type === "new_player" && typeof item.attributes?.display_name === "string") {
      players.set(String(item.id), item.attributes.display_name)
    }
  }

  const board: TennisBoard = {
    rows: [],
    counts: { "Fantasy Score": 0, "Total Games": 0, "Total Games Won": 0 },
    doublesSkipped: 0,
    notPreGameSkipped: 0,
  }
  let tennisRows = 0
  for (const item of payload.data as Json[]) {
    const attributes = item?.attributes
    const relationships = item?.relationships
    // Other leagues carry Fantasy Score too, so the stat name alone would admit them.
    if (!attributes || String(relationships?.league?.data?.id) !== TENNIS_LEAGUE_ID) continue
    tennisRows += 1
    if (!UPLOADED_STATS.has(attributes.stat_type)) continue
    const player = players.get(String(relationships?.new_player?.data?.id))
    if (!player) continue
    if (player.includes("/")) {
      board.doublesSkipped += 1
      continue
    }
    // Not is_live / in_game: PrizePicks sets both on pre-match rows that are also offered live.
    if (attributes.status !== "pre_game") {
      board.notPreGameSkipped += 1
      continue
    }
    board.rows.push({
      player,
      opponent: String(attributes.description ?? ""),
      stat: attributes.stat_type,
      odds_type: attributes.odds_type,
      line: Number(attributes.line_score),
      start_time: attributes.start_time,
      status: attributes.status,
      projection_id: String(item.id),
      game_id: String(relationships?.game?.data?.id ?? ""),
    })
    if (attributes.stat_type !== "Aces") board.counts[attributes.stat_type as TennisPricedStat] += 1
  }

  if (tennisRows === 0) {
    return { ok: false, error: "No tennis rows on this board. Open the projections URL with league_id=5." }
  }
  const priced = TENNIS_STATS.reduce((sum, stat) => sum + board.counts[stat], 0)
  if (priced === 0) {
    return { ok: false, error: "This tennis board has no pre-match Fantasy Score, Total Games or Games Won lines." }
  }
  if (board.rows.length > MAX_BOARD_ROWS) {
    return { ok: false, error: `This board has ${board.rows.length} tennis lines; the limit is ${MAX_BOARD_ROWS}.` }
  }
  return { ok: true, board }
}

export interface TennisResult {
  stat: TennisPricedStat
  player: string
  opponent: string
  start_time: string
  odds_type: TennisOddsType
  line: number
  projection_id: string
  game_id: string
  event_id: string
  flags: string[]
  lean: "over" | "under" | null
  unpriced_reason: "no_book_line" | "ambiguous_match" | "unsupported_format" | null
  tour?: "atp" | "wta"
  p_win?: number
  fav_ml?: number
  total?: number | null
  fit?: string
  p_over?: number
  p_under?: number
  push?: number
  model_mean?: number
  model_median?: number | null
  spread?: number | null
  aces_src?: string
  rule_fs?: number | null
  p_side_bias?: number
  dk_p_over?: number
  dk_p_over_lo?: number
  dk_p_over_hi?: number
  dk_gw_line?: number
  dk_gw_over?: number
  dk_gw_under?: number
  dk_gw_p_over?: number
}

export interface TennisRun {
  run_id: string
  status: "pending" | "ready"
  created_at: string
  board_saved_at: string
  revises: string | null
  odds_snapshot_at: string | null
  odds_age_s: number | null
  refresh: { id: number; status: "running" | "done" | "failed"; started_at: string; error: string | null } | null
  newer_run_id: string | null
  results: TennisResult[]
}

export type TennisGroup = "actionable" | "provisional" | "no_play" | "unpriced" | "started"

export const TENNIS_GROUPS: { id: TennisGroup; label: string; hint: string }[] = [
  { id: "actionable", label: "Actionable", hint: "A lean on a playable side that passes every check, on fresh odds." },
  { id: "provisional", label: "Provisional", hint: "Would be actionable but for stale odds or an assumed ace rate." },
  { id: "no_play", label: "No lean or not playable", hint: "No lean, a Games Won lean under 55%, an under on a goblin or demon, a failed bias check, or a fit that can't be trusted." },
  { id: "unpriced", label: "Unpriced", hint: "No book line to price against." },
  { id: "started", label: "Started", hint: "The match has begun." },
]

/**
 * The lowest probability that counts as a lean worth playing, where a stat has one. Games Won
 * leans cluster just over 50%, below what any PrizePicks slip needs to break even.
 */
export const TENNIS_MIN_PROBABILITY: Partial<Record<TennisPricedStat, number>> = { "Total Games Won": 0.55 }

/** Which group a result belongs in. `now` is checked again here: a board can sit open past a start. */
export function tennisGroup(result: TennisResult, now: Date): TennisGroup {
  const flags = new Set(result.flags)
  if (flags.has("started") || new Date(result.start_time).getTime() <= now.getTime()) return "started"
  if (result.unpriced_reason) return "unpriced"
  // Goblins and demons can only be played over.
  const playable = result.lean === "over" || (result.lean === "under" && result.odds_type === "standard")
  if (!playable || flags.has("fails_bias_check") || flags.has("ml_only") || flags.has("fit_miss")) return "no_play"
  if ((tennisProbability(result) ?? 0) < (TENNIS_MIN_PROBABILITY[result.stat] ?? 0)) return "no_play"
  return flags.has("stale_odds") || flags.has("assumed_aces") ? "provisional" : "actionable"
}

/** The model's probability for the lean, or for the likelier side when there is no lean. */
export function tennisProbability(result: TennisResult): number | null {
  if (result.p_over == null || result.p_under == null) return null
  if (result.lean === "over") return result.p_over
  if (result.lean === "under") return result.p_under
  return Math.max(result.p_over, result.p_under)
}

const ODDS_TYPE_ORDER: Record<TennisOddsType, number> = { standard: 0, goblin: 1, demon: 2 }

/**
 * Results per group, each by model probability. With every odds type shown, a group is ordered
 * by type first: without payouts, P on a goblin and P on a standard aren't comparable.
 */
export function groupTennisResults(results: TennisResult[], now: Date, byTypeFirst: boolean): Record<TennisGroup, TennisResult[]> {
  const groups: Record<TennisGroup, TennisResult[]> = { actionable: [], provisional: [], no_play: [], unpriced: [], started: [] }
  for (const result of results) groups[tennisGroup(result, now)].push(result)
  for (const rows of Object.values(groups)) {
    rows.sort((a, b) =>
      (byTypeFirst ? ODDS_TYPE_ORDER[a.odds_type] - ODDS_TYPE_ORDER[b.odds_type] : 0)
      || (tennisProbability(b) ?? -1) - (tennisProbability(a) ?? -1)
      || a.start_time.localeCompare(b.start_time)
      || a.player.localeCompare(b.player),
    )
  }
  return groups
}

export const TENNIS_UNPRICED_LABELS: Record<NonNullable<TennisResult["unpriced_reason"]>, string> = {
  no_book_line: "no book line",
  ambiguous_match: "two matches fit this name",
  unsupported_format: "best of five",
}

export const TENNIS_FLAG_LABELS: Record<string, string> = {
  assumed_aces: "assumed aces",
  stale_odds: "stale odds",
  fit_miss: "fit miss",
  fails_bias_check: "fails bias check",
  ml_only: "moneyline only",
  blowout: "blowout",
  juice_over: "DK juice over",
  juice_under: "DK juice under",
  over_only: "over only",
  started: "started",
}
