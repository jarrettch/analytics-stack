import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  transpilePackages: ["@repo/ui"],
  async rewrites() {
    return [
      {
        source: "/api/nba-cdn/:path*",
        destination: "https://cdn.nba.com/static/json/liveData/:path*",
      },
    ]
  },
}

export default nextConfig