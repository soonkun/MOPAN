import { redirect } from "next/navigation";

// force-dynamic is not optional here. Statically prerendered, this page answers
// 307 with NO Location header at all: the redirect is encoded in the RSC payload
// as NEXT_REDIRECT and only runs once client JS boots, so curl, `curl -L`, the
// Task 24 smoke test and any health check see a dead 307 carrying an error
// shell. Rendered per request, Next emits a real 307 + Location: /chat.
export const dynamic = "force-dynamic";

// Without this page, http://localhost:3000/ - the URL the README tells you to
// open - is a 404.
export default function RootPage() {
  redirect("/chat");
}
