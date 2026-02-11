import Link from "next/link";
import roster from "../src/data/roster.json";
import { PlayerHex } from "@repo/ui/PlayerHex";

const pitchers = roster.filter((p) => p.position === "P");
const twoWay = roster.filter((p) => p.position === "TWP");
const catchers = roster.filter((p) => p.position === "C");
const infielders = roster.filter((p) =>
  ["1B", "2B", "3B", "SS"].includes(p.position)
);
const outfielders = roster.filter((p) =>
  ["LF", "CF", "RF", "OF"].includes(p.position)
);

const sections = [
  { title: "Pitchers", players: pitchers },
  { title: "Two-Way Players", players: twoWay },
  { title: "Catchers", players: catchers },
  { title: "Infielders", players: infielders },
  { title: "Outfielders", players: outfielders },
];

export default function Page() {
  return (
    <main className="min-h-screen bg-slate-950 text-white px-8 py-16 flex justify-center">
      <div className="max-w-5xl w-full">
        <h1 className="text-4xl font-bold mb-2 text-center text-blue-500">
          Dodgers 40-Man Roster
        </h1>
        <p className="text-center text-slate-400 mb-8">
          {roster.length} Players
        </p>

        {sections.map((section) => (
          <section key={section.title} className="mb-12 flex flex-col gap-y-4">
            <h2 className="text-2xl font-bold text-slate-300 border-b border-slate-700 pb-2">
              {section.title}
              <span className="text-sm font-normal text-slate-500 ml-2">
                ({section.players.length})
              </span>
            </h2>
            <div className="flex flex-wrap justify-center gap-4">
              {section.players.map((player) => (
                <Link
                  key={player.id}
                  href={`/player/${player.id}`}
                >
                  <PlayerHex
                    playerId={player.id}
                    name={player.name}
                    number={player.jerseyNumber}
                    position={player.position}
                    imageUrl={player.mlbHeadshot}
                    rating={85}
                  />
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
