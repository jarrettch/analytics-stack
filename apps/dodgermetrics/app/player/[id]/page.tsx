import Link from "next/link";
import roster from "../../../src/data/roster.json";
import allStats from "../../../src/data/stats.json";

interface PlayerPageProps {
  params: Promise<{ id: string }>;
}

type StatsRecord = Record<string, string | number>;

interface PlayerStats {
  hitting?: StatsRecord;
  pitching?: StatsRecord;
}

export default async function PlayerPage({ params }: PlayerPageProps) {
  const { id } = await params;
  const player = roster.find((p) => p.id === Number(id));
  const stats = (allStats as Record<string, PlayerStats>)[id];

  if (!player) {
    return (
      <main className="min-h-screen bg-slate-950 text-white px-8 py-16 flex justify-center">
        <div className="text-center">
          <h1 className="text-4xl font-bold mb-4">Player Not Found</h1>
          <Link href="/" className="text-blue-500 hover:underline">
            Back to Roster
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-white px-8 py-16 flex justify-center">
      <div className="max-w-4xl w-full">
        <Link href="/" className="text-blue-500 hover:underline mb-8 inline-block">
          ← Back to Roster
        </Link>

        <div className="flex flex-col md:flex-row gap-8 items-center md:items-start">
          {/* Player Image */}
          <div className="flex-shrink-0">
            <img
              src={player.mlbHeadshot}
              alt={player.name}
              className="w-48 h-48 rounded-lg object-cover bg-slate-800"
            />
          </div>

          {/* Player Info */}
          <div className="flex-1 text-center md:text-left">
            <h1 className="text-4xl font-bold mb-2">{player.name}</h1>
            <div className="flex flex-wrap gap-4 justify-center md:justify-start text-slate-400 mb-6">
              <span className="bg-slate-800 px-3 py-1 rounded">#{player.jerseyNumber || "N/A"}</span>
              <span className="bg-slate-800 px-3 py-1 rounded">{player.position}</span>
              <span className="bg-slate-800 px-3 py-1 rounded">
                {player.status === "A" ? "Active" : player.status}
              </span>
            </div>
          </div>
        </div>

        {/* Stats Section */}
        <div className="mt-8 space-y-6">
          {stats?.hitting && (
            <div className="bg-slate-900 rounded-lg p-6">
              <h2 className="text-xl font-bold mb-4 text-slate-300">2025 Hitting Stats</h2>
              <div className="grid grid-cols-3 md:grid-cols-6 gap-4 text-center">
                <StatBox label="AVG" value={stats.hitting.avg} />
                <StatBox label="HR" value={stats.hitting.homeRuns} />
                <StatBox label="RBI" value={stats.hitting.rbi} />
                <StatBox label="OPS" value={stats.hitting.ops} />
                <StatBox label="H" value={stats.hitting.hits} />
                <StatBox label="R" value={stats.hitting.runs} />
                <StatBox label="2B" value={stats.hitting.doubles} />
                <StatBox label="3B" value={stats.hitting.triples} />
                <StatBox label="BB" value={stats.hitting.baseOnBalls} />
                <StatBox label="SO" value={stats.hitting.strikeOuts} />
                <StatBox label="SB" value={stats.hitting.stolenBases} />
                <StatBox label="G" value={stats.hitting.gamesPlayed} />
              </div>
            </div>
          )}

          {stats?.pitching && (
            <div className="bg-slate-900 rounded-lg p-6">
              <h2 className="text-xl font-bold mb-4 text-slate-300">2025 Pitching Stats</h2>
              <div className="grid grid-cols-3 md:grid-cols-6 gap-4 text-center">
                <StatBox label="ERA" value={stats.pitching.era} />
                <StatBox label="W" value={stats.pitching.wins} />
                <StatBox label="L" value={stats.pitching.losses} />
                <StatBox label="SV" value={stats.pitching.saves} />
                <StatBox label="IP" value={stats.pitching.inningsPitched} />
                <StatBox label="SO" value={stats.pitching.strikeOuts} />
                <StatBox label="WHIP" value={stats.pitching.whip} />
                <StatBox label="BB" value={stats.pitching.baseOnBalls} />
                <StatBox label="H" value={stats.pitching.hits} />
                <StatBox label="K/9" value={stats.pitching.strikeoutsPer9Inn} />
                <StatBox label="BB/9" value={stats.pitching.walksPer9Inn} />
                <StatBox label="G" value={stats.pitching.gamesPlayed} />
              </div>
            </div>
          )}

          {!stats && (
            <div className="bg-slate-900 rounded-lg p-6">
              <h2 className="text-xl font-bold mb-4 text-slate-300">Stats</h2>
              <p className="text-slate-500">No 2025 stats available for this player.</p>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}

function StatBox({ label, value }: { label: string; value: string | number | undefined }) {
  return (
    <div className="bg-slate-800 rounded p-3">
      <div className="text-xs text-slate-400 uppercase">{label}</div>
      <div className="text-xl font-bold text-white">{value ?? "-"}</div>
    </div>
  );
}
