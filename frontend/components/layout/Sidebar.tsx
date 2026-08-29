"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Conversation, User } from "@/lib/types";

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

  const load = useCallback(async () => {
    try {
      const [me, list] = await Promise.all([
        apiFetch<User>("/api/auth/me"),
        apiFetch<Conversation[]>("/api/conversations"),
      ]);
      setUser(me);
      setConversations(list);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  // pathname is a dependency on purpose: the chat page creates a conversation
  // and then navigates to /chat/{id}. Refetching on that navigation is what
  // puts the new title into the history list.
  useEffect(() => {
    void load();
  }, [load, pathname]);

  // Without these the drawer is only technically keyboard-usable: nothing moves
  // focus into it on open, so dismissing it means tabbing past every history
  // link to reach the closing overlay - ~34 presses with 30 conversations.
  // Escape closes it, and focus returns to the toggle that opened it.
  useEffect(() => {
    if (!open) return;
    drawerRef.current?.querySelector<HTMLElement>("a, button")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        return;
      }
      // The drawer covers the page but does not remove it from the tab order,
      // so without this Tab walks into content hidden behind the overlay.
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>("a, button");
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
      toggleRef.current?.focus();
    };
  }, [open]);

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

  const content = (
    <nav className="flex h-full w-64 flex-col border-r border-gray-200 bg-gray-50 p-3">
      <div className="mb-4 px-3 text-sm font-semibold text-gray-500">MOPAN</div>
      {navLinks.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          onClick={() => setOpen(false)}
          className={`rounded px-3 py-2 text-sm hover:bg-gray-200 ${
            pathname === link.href ? "bg-gray-200 font-medium" : ""
          }`}
        >
          {link.label}
        </Link>
      ))}

      <div className="mt-4 flex-1 overflow-y-auto">
        <div className="mb-1 px-3 text-xs tracking-wide text-gray-400">대화 기록</div>
        {error && <ErrorBanner message={error} />}
        {!error && conversations?.length === 0 && (
          <p className="px-3 py-2 text-xs text-gray-400">아직 대화가 없습니다.</p>
        )}
        {conversations?.map((c) => (
          <Link
            key={c.id}
            href={`/chat/${c.id}`}
            onClick={() => setOpen(false)}
            className="block truncate rounded px-3 py-2 text-sm hover:bg-gray-200"
          >
            {c.title}
          </Link>
        ))}
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
        <button
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
            aria-label="메뉴 닫기"
            className="flex-1 bg-black/30"
            onClick={() => setOpen(false)}
          />
        </div>
      )}
    </>
  );
}
