import type { NextConfig } from "next";

const API = process.env.PIQ_API_URL ?? "http://127.0.0.1:8000";
const dev = process.env.NODE_ENV !== "production";

// The browser may only talk to this origin. No CDNs, fonts, analytics or remote images.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  `connect-src 'self'${dev ? " ws://127.0.0.1:3000 ws://localhost:3000" : ""}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const config: NextConfig = {
  poweredByHeader: false,
  // Local inference can take a minute or more with a reasoning model; the default proxy timeout is ~30s.
  experimental: { proxyTimeout: 300_000 },
  // The browser calls same-origin /api/*; Next forwards it to the API on loopback.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/health", destination: `${API}/health` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default config;
