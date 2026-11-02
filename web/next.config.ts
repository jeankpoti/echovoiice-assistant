import type { NextConfig } from "next";

// The FastAPI backend. Requests to /v1/* are proxied there so the browser stays same-origin.
const API_URL = process.env.API_URL || "http://localhost:8080";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${API_URL}/v1/:path*` }];
  },
};

export default nextConfig;
