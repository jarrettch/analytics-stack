import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  transpilePackages: ["@repo/ui"],
  async rewrites() {
    return [
      {
        source: "/api/mlb-stats/:path*",
        destination: "https://statsapi.mlb.com/api/v1/:path*",
      },
    ]
  },
}

export default nextConfig