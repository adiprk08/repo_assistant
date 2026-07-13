/** @type {import('next').NextConfig} */

// Same-origin proxy: the browser calls /api/* on this origin, Next forwards to the
// FastAPI service server-side. This keeps the session cookie first-party and
// sidesteps cross-origin SameSite=None;Secure friction (docs/adr/0023).
const API_ORIGIN = process.env.API_ORIGIN || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/:path*` }];
  },
};

export default nextConfig;
