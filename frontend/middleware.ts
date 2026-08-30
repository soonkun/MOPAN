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
    const target = pathname + request.nextUrl.search;
    url.pathname = "/login";
    // clone() carries the original query string over; drop it so /login gets
    // `next` alone rather than the target page's params leaking beside it.
    url.search = "";
    // Keep where they were headed so a deep link survives the login round trip.
    url.searchParams.set("next", target);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // `api` MUST stay excluded: middleware runs before next.config.js rewrites, so
  // matching /api/* here would answer every proxied API call with a redirect to
  // the login HTML page instead of forwarding it to the backend.
  //
  // `theme.js` MUST stay excluded for the same shape of reason, measured: it is
  // a static file under public/, so without it here an unauthenticated visitor
  // requesting the pre-paint theme script got 307 -> /login and the browser
  // parsed the login page's HTML as JavaScript ("Unexpected token '<'"). The
  // script therefore never ran on /login or /register - the two routes a
  // logged-out user actually sees - and a dark-theme user got a white flash on
  // every visit to them. It carries no user data and gates nothing.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|theme.js).*)"],
};
