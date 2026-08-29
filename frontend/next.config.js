/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,
  // Same-origin API proxy. The browser only ever calls /api/* on this origin, so:
  //  - CORS never applies
  //  - SameSite=Lax session cookies are sent normally, including behind a tunnel
  //  - no backend URL reaches the client bundle at all
  //  - one Cloudflare Tunnel on :3000 exposes the whole app
  //
  // API_INTERNAL_URL is read at BUILD time, not at run time, and this was
  // measured: `next build` evaluates rewrites() once and writes the resolved
  // destination as a literal string into .next/routes-manifest.json, which is
  // what `next start` serves from. Setting the variable in the environment of
  // the running server changes nothing - a server started with
  // API_INTERNAL_URL=http://127.0.0.1:8123 still proxied to localhost:8000.
  // So frontend/Dockerfile takes it as an ARG and docker-compose.yml passes it
  // under build.args. Putting it back under compose `environment:` would be the
  // same silent failure NEXT_PUBLIC_API_BASE_URL had, one layer down: the
  // container would proxy to its own empty port 8000 and every call would fail.
  async rewrites() {
    const backend = process.env.API_INTERNAL_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
