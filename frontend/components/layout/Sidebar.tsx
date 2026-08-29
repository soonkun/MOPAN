"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Conversation, User } from "@/lib/types";

// The trap has to enumerate everything focusable inside the drawer, not just
// what happens to be in it today: with "a, button" the first <input> added to
// the sidebar (a history filter) becomes an element the trap does not know
// about, so `last` stops being the real last stop and Tab escapes the dialog.
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  // null means "not loaded yet", which is not the same as an empty list. With
  // [] as the initial value every page load flashes "아직 대화가 없습니다."
  // for the length of the fetch, including for users who do have conversations.
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Separate from `error` on purpose. The history region is scrollable, so an
  // ErrorBanner rendered at its top is off-screen for anyone with enough
  // conversations to have scrolled - measured 0 visible pixels at 1280x800
  // with 31 conversations. A logout failure has to report next to the button
  // that was clicked. It is also cleared per attempt rather than by load().
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  // allSettled, not all: these two requests are independent and Promise.all
  // rejects the pair on the first failure, so a 500 from /api/conversations
  // threw away a perfectly good /api/auth/me and left the footer showing the
  // blank U+00A0 placeholder - a transient history-list error made the user
  // look logged out. Each result is now applied on its own. The list is still set
  // in the same tick as the user, so `conversations` stays null - never [] -
  // until the fetch resolves, which is what keeps the empty state from
  // flashing. The conversations failure is checked first because the banner
  // renders in the history region, where its own error belongs.
  const load = useCallback(async () => {
    const [me, list] = await Promise.allSettled([
      apiFetch<User>("/api/auth/me"),
      apiFetch<Conversation[]>("/api/conversations"),
    ]);
    if (me.status === "fulfilled") setUser(me.value);
    if (list.status === "fulfilled") setConversations(list.value);
    const failed = [list, me].find(
      (result): result is PromiseRejectedResult => result.status === "rejected",
    );
    setError(failed ? errorMessage(failed.reason) : null);
  }, []);

  // pathname is a dependency on purpose: the chat page creates a conversation
  // and then router.replace()s to /chat/{id}, and the new title has to reach
  // this list. Measured on `next start`, that particular navigation is a full
  // document load, which remounts this component and reloads the list anyway;
  // the dependency is what covers the soft navigations - every click between
  // conversations - and what would still cover the replace if it became one.
  useEffect(() => {
    void load();
  }, [load, pathname]);

  // Without these the drawer is only technically keyboard-usable: nothing moves
  // focus into it on open, so dismissing it means tabbing past every history
  // link to reach the closing overlay - ~34 presses with 30 conversations.
  // Escape closes it, and focus returns to the toggle that opened it.
  useEffect(() => {
    if (!open) return;
    drawerRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    // aria-modal="true" is a promise that nothing outside the dialog is
    // reachable; `inert` is what makes it true for the DOM rather than only
    // for AT that honours the attribute - measured with the drawer open,
    // <main> still held 4 focusable elements. It is set from here with
    // setAttribute rather than as a JSX prop because `open` lives in this
    // client component while <main> is rendered by (app)/layout.tsx, which is
    // a server component. The body lock is the pointer half of the same bug:
    // the drawer is `fixed`, so without it the page behind scrolls on touch.
    const main = document.getElementById("app-main");
    main?.setAttribute("inert", "");
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        return;
      }
      // The drawer covers the page but does not remove it from the tab order,
      // so without this Tab walks into content hidden behind the overlay.
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      main?.removeAttribute("inert");
      document.body.style.overflow = previousOverflow;
      toggleRef.current?.focus();
    };
  }, [open]);

  // `md:hidden` only stops the drawer from being *displayed* above 768px; the
  // state stays true, so resizing 390 -> 1280 -> 390 with it open brought the
  // drawer back on the way down without the user reopening it. 768px is
  // Tailwind's `md`, the same breakpoint the classes use.
  useEffect(() => {
    const docked = window.matchMedia("(min-width: 768px)");
    const onChange = () => {
      if (docked.matches) setOpen(false);
    };
    docked.addEventListener("change", onChange);
    return () => docked.removeEventListener("change", onChange);
  }, []);

  async function handleLogout() {
    // Navigate only on success. A `finally` here lands the user on /login after
    // a failed request - with mopan_session still in the browser and the Redis
    // session still valid, because neither delete_cookie nor delete_session
    // ran. "Logged out" with a live session is the worst outcome available, so
    // a failure stays put and says so next to the button that was clicked.
    setLogoutError(null);
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch (err) {
      setLogoutError(errorMessage(err));
      return;
    }
    router.push("/login");
    // The App Router caches rendered segments client-side. Without this the
    // authenticated pages stay in that cache after the cookie is gone.
    router.refresh();
  }

  const navLinks = [
    { href: "/chat", label: "새 대화" },
    { href: "/documents", label: "문서" },
  ];

  // Rendered only for an admin. Both screens are admin-only on the server too -
  // every endpoint behind them answers 403 관리자 권한이 필요합니다. - so this is
  // about not offering a link that leads to a refusal, not about access.
  const adminLinks = [
    { href: "/collections", label: "분류 관리" },
    { href: "/users", label: "사용자 관리" },
  ];

  // Same markup for both groups: the active styling and aria-current below are
  // the one thing a second copy would eventually get wrong.
  const navLink = (link: { href: string; label: string }) => {
    const active = pathname === link.href;
    return (
      <Link
        key={link.href}
        href={link.href}
        onClick={() => setOpen(false)}
        // The background alone said "you are here" to sighted users only.
        aria-current={active ? "page" : undefined}
        className={`rounded px-3 py-2 text-sm hover:bg-gray-200 ${
          active ? "bg-gray-200 font-medium" : ""
        }`}
      >
        {link.label}
      </Link>
    );
  };

  const content = (
    // aria-label because the MOPAN line above is a <div>, so the landmark had
    // no accessible name and announced as a bare "navigation". 대화 기록 stays
    // a <div> rather than becoming a heading: the sidebar precedes <main> in
    // the DOM, so a heading here would sit above every page's <h1>.
    <nav
      aria-label="주 메뉴"
      className="flex h-full w-64 flex-col border-r border-gray-200 bg-gray-50 p-3"
    >
      <div className="mb-4 px-3 text-sm font-semibold text-gray-500">MOPAN</div>
      {navLinks.map(navLink)}

      {/* `user` is null until /api/auth/me lands, so a non-admin never sees this
          appear and then vanish. flex-col on the wrapper because the links are
          <a> elements: as direct children of this flex column they stack on
          their own, but inside a plain div they would run side by side. */}
      {user?.role === "admin" && (
        <div className="mt-4">
          <div className="mb-1 px-3 text-xs tracking-wide text-gray-400">관리</div>
          <div className="flex flex-col">{adminLinks.map(navLink)}</div>
        </div>
      )}

      <div className="mt-4 flex-1 overflow-y-auto">
        <div className="mb-1 px-3 text-xs tracking-wide text-gray-400">대화 기록</div>
        {error && <ErrorBanner message={error} />}
        {!error && conversations?.length === 0 && (
          <p className="px-3 py-2 text-xs text-gray-400">아직 대화가 없습니다.</p>
        )}
        {/* Which conversation you are in is the one piece of state a history
            list exists to convey, and the links carried only a hover style:
            measured at /chat/c3, every link's computed background was
            rgba(0,0,0,0). Same treatment as the nav links above. */}
        {conversations?.map((c) => {
          const active = pathname === `/chat/${c.id}`;
          return (
            <Link
              key={c.id}
              href={`/chat/${c.id}`}
              onClick={() => setOpen(false)}
              aria-current={active ? "page" : undefined}
              className={`block truncate rounded px-3 py-2 text-sm hover:bg-gray-200 ${
                active ? "bg-gray-200 font-medium" : ""
              }`}
            >
              {c.title}
            </Link>
          );
        })}
      </div>

      <div className="mt-3 border-t border-gray-200 pt-3">
        {/* The placeholder is U+00A0, not an ASCII space: a plain space is
            collapsible, so the line gets no line box and is 0px tall until
            /api/auth/me lands - at which point it grows 16px and shoves
            로그아웃 down under the pointer already resting on it. */}
        <div className="truncate px-3 text-xs text-gray-500">
          {user ? `${user.email}${user.role === "admin" ? " · 관리자" : ""}` : "\u00a0"}
        </div>
        {logoutError && <ErrorBanner message={logoutError} />}
        {/* type="button" on every button in this file: the default is
            "submit", which is a live bug the moment one of them ends up
            inside a <form>. */}
        <button
          type="button"
          onClick={() => void handleLogout()}
          className="mt-2 w-full rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-200"
        >
          로그아웃
        </button>
      </div>
    </nav>
  );

  return (
    <>
      {/* Not rendered while the drawer is open: at z-20 it sits *under* the
          z-30 drawer, so a pointer user cannot reach it while a keyboard user
          can still focus it and press it for nothing. No aria-expanded either
          - it only opens; the drawer closes via its overlay or Escape. */}
      {!open && (
        <button
          ref={toggleRef}
          type="button"
          aria-label="메뉴 열기"
          aria-controls="sidebar-drawer"
          className="fixed left-2 top-2 z-20 rounded border border-gray-300 bg-white px-2 py-1 text-sm md:hidden"
          onClick={() => setOpen(true)}
        >
          ☰
        </button>
      )}
      <div className="hidden md:block">{content}</div>
      {open && (
        <div
          id="sidebar-drawer"
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-label="메뉴"
          className="fixed inset-0 z-30 flex md:hidden"
        >
          <div className="relative">{content}</div>
          {/* A button, not a div: this overlay is the only way to close the
              drawer, and as a div it is unreachable without a pointer. */}
          <button
            type="button"
            aria-label="메뉴 닫기"
            className="flex-1 bg-black/30"
            onClick={() => setOpen(false)}
          />
        </div>
      )}
    </>
  );
}
