import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = ["/login", "/register"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }
  // Presence check only - the backend is the authority on validity. Without this
  // an unauthenticated visitor sees a functional-looking but empty shell.
  if (!request.cookies.get("mopan_session")) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // `api` MUST stay excluded: middleware runs before next.config.js rewrites, so
  // matching /api/* here would answer every proxied API call with a redirect to
  // the login HTML page instead of forwarding it to the backend.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
