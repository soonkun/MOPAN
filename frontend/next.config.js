/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,
  experimental: {
    // The rewrite proxy below buffers each request body through Next's own
    // router process, which caps it at 10MB by default (getCloneableBody in
    // next/dist/server/lib/router-utils/resolve-routes.js - the same code path
    // in `next dev` and `next start`). The backend advertises a 50MB limit, so
    // without this every upload between 10MB and 50MB - an ordinary scanned PDF
    // - died in the proxy. Measured: a 12MB POST to /api/documents returned
    // HTTP 500 "Internal Server Error" through :3100 and HTTP 400 with the
    // backend's Korean detail when sent to :8000 directly; the dev server
    // logged "Request body exceeded 10MB" and "socket hang up".
    // Set ABOVE settings.max_upload_size_mb, not equal to it: an over-limit
    // upload has to reach the backend to be told 파일이 최대 크기 50MB를
    // 초과했습니다. rather than being truncated into a generic 500.
    // ponytail: 64mb is the ceiling. Nothing in this slice sits in front of Next
    // to keep in step - Task 24 tunnels cloudflared straight at it - but a
    // deployment that does add a reverse proxy has to raise that proxy's own body
    // limit (nginx: client_max_body_size) to match.
    middlewareClientMaxBodySize: "64mb",
  },
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
  // /agents was a real, linked, bookmarked screen until this slice renamed the
  // concept. The route is gone; a 404 for somebody's saved link is not the
  // apology the rename owes them. A REDIRECT rather than a second page that
  // renders the same thing: there is one screen, at one address, and the old
  // address says so.
  //
  // Not permanent (308). A 301/308 is cached by a browser more or less forever,
  // so if /agents ever means something again it would be unreachable from every
  // machine that had followed this once.
  async redirects() {
    return [{ source: "/agents", destination: "/workflows", permanent: false }];
  },
  // See the note above rewrites() for why this file is where deployment
  // behaviour lives. Measured before this existed: a prerendered document came
  // back with `Cache-Control: s-maxage=31536000` and no browser directive at
  // all, so Safari cached it heuristically. The document names content-hashed
  // chunks, so the next deploy left a returning phone asking for chunk files
  // that no longer exist - every one a 404, and the app rendered as a composer
  // at the top of a tall white void. It looked like the whole front end had
  // broken, and it could not be reproduced from a machine that had the new
  // document.
  //
  // The two kinds of asset want opposite things:
  //   /_next/static - content-hashed, so a new build is a new URL. Immutable.
  //   everything else - the HTML document. Must be revalidated, or a deploy
  //                     never reaches anyone who has visited before.
  //
  // `no-cache`, not `no-store`: the browser may KEEP the copy, it just may not
  // use it without asking. That costs one conditional request and usually
  // returns 304; no-store would re-download the document on every navigation
  // for nothing.
  async headers() {
    return [
      {
        source: "/_next/static/:path*",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
      {
        // Everything that is not a hashed build asset. /api/* is proxied by
        // rewrites() and answers with the backend's own headers, so it is
        // excluded rather than being given a caching policy it did not ask for.
        source: "/((?!_next/static|api/).*)",
        headers: [{ key: "Cache-Control", value: "no-cache, must-revalidate" }],
      },
    ];
  },
};
