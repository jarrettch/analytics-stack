import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  transpilePackages: ["@repo/ui"],
  async rewrites() {
    return [
      {
        source: "/api/nba-cdn/:path*",
        destination: "https://cdn.nba.com/static/json/liveData/:path*",
      },
      {
        source: "/api/mlb-stats/:path*",
        destination: "https://statsapi.mlb.com/api/v1/:path*",
      },
    ]
  },
}

export default nextConfig