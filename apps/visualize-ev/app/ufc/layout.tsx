import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "UFC Next Card | Visualize EV",
  description:
    "The next UFC card in fight order, with DraftKings or FanDuel moneylines, de-vigged win probabilities, total rounds, and go-the-distance prices.",
};

export default function UFCLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
