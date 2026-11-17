import type { Metadata } from "next";
import localFont from "next/font/local";
import { ScoresStrip } from "@repo/ui";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
});

export const metadata: Metadata = {
  title: "Visualize EV",
  description: "Visualize EV - See your edge",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" style={{ colorScheme: 'dark' }}>
      <body className={`${geistSans.variable} ${geistMono.variable} bg-background text-foreground antialiased`}>
        <ScoresStrip apiBaseUrl={process.env.NEXT_PUBLIC_API_URL} />
        {children}
      </body>
    </html>
  );
}