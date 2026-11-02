import type { NextConfig } from "next";

// The FastAPI backend. Requests to /v1/* are proxied there so the browser stays same-origin.
const API_URL = process.env.API_URL || "http://localhost:8080";

const nextConfig: NextConfig = {
  output: "standalone",
  // A turn can take a while (speech recognition, Muse Spark with one retry, the voice);
  // the default 30 s proxy timeout cut slow ones off with a 500
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${API_URL}/v1/:path*` }];
  },
};

export default nextConfig;
