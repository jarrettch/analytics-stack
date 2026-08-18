import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Oh-K Corral · MLB Pitcher Strikeout Odds | Visualize EV",
  description:
    "MLB pitcher strikeout prop board: main K lines by sportsbook, best available price, intraday line movement, and alternate ladders — built on observed odds snapshots.",
};

export default function PitcherStrikeoutOddsLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
