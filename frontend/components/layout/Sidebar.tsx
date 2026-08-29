"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
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

  async function handleLogout() {
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } finally {
      router.push("/login");
      // The App Router caches rendered segments client-side. Without this the
      // authenticated pages stay in that cache after the cookie is gone.
      router.refresh();
    }
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
        <div className="truncate px-3 text-xs text-gray-500">
          {user ? `${user.email}${user.role === "admin" ? " · 관리자" : ""}` : " "}
        </div>
        <button
          onClick={handleLogout}
          className="mt-2 w-full rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-200"
        >
          로그아웃
        </button>
      </div>
    </nav>
  );

  return (
    <>
      <button
        aria-label="메뉴 열기"
        aria-expanded={open}
        className="fixed left-2 top-2 z-20 rounded border border-gray-300 bg-white px-2 py-1 text-sm md:hidden"
        onClick={() => setOpen(true)}
      >
        ☰
      </button>
      <div className="hidden md:block">{content}</div>
      {open && (
        <div className="fixed inset-0 z-30 flex md:hidden">
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
