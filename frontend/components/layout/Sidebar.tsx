"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import AccountMenu from "@/components/layout/AccountMenu";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type {
  Branding, Conversation, User } from "@/lib/types";

// The trap has to enumerate everything focusable inside the drawer, not just
// what happens to be in it today: with "a, button" the first <input> added to
// the sidebar (a history filter) becomes an element the trap does not know
// about, so `last` stops being the real last stop and Tab escapes the dialog.
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** A glyph per function, keyed by the route it stands for.
 *
 * Inline SVG on `currentColor`, for the reasons every icon in this app is
 * it, and deliberately NOT an icon package: a dozen paths do not justify a
 * dependency, a bundle, or a second source of truth for what "워크플로우" looks
 * like. The agent shield is the same path the composer's agent control draws,
 * because they mean the same thing and a user should not have to learn two.
 *
 * Every one of these is DECORATIVE. The label beside it is the accessible name
 * and the icon repeats it, so the <svg> is aria-hidden and contributes nothing
 * to the name - a link that announced "문서 문서" would be the alternative.
 *
 * The conversation history rows deliberately get none. They are titles, not
 * functions: one identical glyph repeated down twenty rows says nothing that
 * distinguishes them, and it would spend 32px of a 224px row on decoration in
 * the one place where the text already truncates. */
const NAV_ICON: Record<string, React.ReactNode> = {
  // 새 대화 - a pencil, the compose mark.
  "/chat": (
    <>
      <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17Z" />
      <path d="M14.5 7.5 16.5 9.5" />
    </>
  ),
  // 문서 - a page with a folded corner.
  "/documents": (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h4" />
    </>
  ),
  // 분류 관리 - a folder, which is what a collection is.
  "/collections": <path d="M3 8a2 2 0 0 1 2-2h3.2l1.8 2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />,
  // 사용자 관리 - two people.
  "/users": (
    <>
      <path d="M15 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-4A3.5 3.5 0 0 0 4 18.5V20" />
      <circle cx="9.5" cy="8" r="3.2" />
      <path d="M17 11.2a3 3 0 0 0 0-6" />
      <path d="M20 20v-1.5a3.5 3.5 0 0 0-2.6-3.4" />
    </>
  ),
  // 프롬프트 관리 - a speech bubble with lines in it: the text the system says.
  "/prompts": (
    <>
      <path d="M20 12a7 7 0 0 1-7 7H8.5L4 22v-4.2A7 7 0 0 1 4 12v-.5A6.5 6.5 0 0 1 10.5 5h3A6.5 6.5 0 0 1 20 11.5Z" />
      <path d="M8.5 10.5h7M8.5 14h4" />
    </>
  ),
  // 워크플로우 - the shield-check the composer's workflow control uses.
  "/workflows": (
    <>
      <path d="M12 3 4 7v5c0 4.4 3.2 8.2 8 9 4.8-.8 8-4.6 8-9V7l-8-4Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  // MCP 서버 관리 - a two-unit server rack with its status lamps.
  "/mcp": (
    <>
      <rect x="3.5" y="4" width="17" height="6.5" rx="1.8" />
      <rect x="3.5" y="13.5" width="17" height="6.5" rx="1.8" />
      <path d="M7 7.2h.01M7 16.7h.01" />
    </>
  ),
  // 고급 설정 - sliders, not a cog: these are values an admin tunes.
  "/settings": (
    <>
      <path d="M4 7h6M14 7h6M4 17h10M18 17h2" />
      <circle cx="12" cy="7" r="2" />
      <circle cx="16" cy="17" r="2" />
    </>
  ),
  // 로그아웃 - out through the door.
  logout: (
    <>
      <path d="M15 12H4" />
      <path d="m7.5 8.5-3.5 3.5 3.5 3.5" />
      <path d="M11 5h6a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-6" />
    </>
  ),
};

function NavIcon({ name }: { name: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-5 w-5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {NAV_ICON[name]}
    </svg>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [branding, setBranding] = useState<Branding | null>(null);
  const [accountOpen, setAccountOpen] = useState(false);
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
  // Which row's ⋯ menu is open, which row is being renamed, and which row the
  // confirmation dialog is about. Three separate ids rather than one union,
  // because a rename and a delete are never in flight at once but the menu that
  // opened either of them has already closed by then.
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  // Escape discards a rename; a click away saves it. Both unmount the input, so
  // this is what a blur fired during that removal is checked against.
  const cancelRenameRef = useRef(false);

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
    // 브랜딩은 장식이라 실패해도 화면을 세우지 않는다 - 기본 문구로 그린다.
    void apiFetch<Branding>("/api/branding").then(setBranding).catch(() => {});
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

  async function commitRename(id: string) {
    const title = renameValue.trim();
    setRenamingId(null);
    // Nothing to save, and the server would answer 422 for a blank title. The
    // row keeps the name it had.
    if (!title) return;
    try {
      await apiFetch(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
    } catch (err) {
      setError(errorMessage(err));
      return;
    }
    // Reload rather than patching the array in place: PATCH bumps updated_at,
    // and this list is ordered by it, so the renamed row moves.
    await load();
  }

  async function confirmDelete(conversation: Conversation) {
    await apiFetch(`/api/conversations/${conversation.id}`, { method: "DELETE" });
    // Off the conversation that no longer exists, before the list reloads:
    // staying put would leave /chat/{id} rendering a 404 banner over an empty
    // transcript. push, not replace - Back should return to where they were.
    if (pathname === `/chat/${conversation.id}`) router.push("/chat");
    await load();
  }

  const navLinks = [
    { href: "/chat", label: "새 대화" },
    { href: "/documents", label: "문서" },
  ];

  // Rendered only for an admin. All three screens are admin-only on the server too -
  // every endpoint behind them answers 403 관리자 권한이 필요합니다. - so this is
  // about not offering a link that leads to a refusal, not about access.
  const adminLinks = [
    { href: "/collections", label: "분류 관리" },
    { href: "/users", label: "사용자 관리" },
    { href: "/prompts", label: "프롬프트 관리" },
    { href: "/mcp", label: "MCP 서버 관리" },
    // After MCP on purpose: an agent is assembled out of the collections,
    // prompts and MCP tools above it, so the order follows the order the work
    // is actually done in.
    { href: "/workflows", label: "워크플로우" },
    { href: "/settings", label: "고급 설정" },
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
        // gap-3 (12px) beside a 20px icon inside px-4 leaves 224 - 32 = 192px
        // for the label in a 280px sidebar. The longest is MCP 서버 관리 at
        // ~84px, so nothing here comes near truncating; no `truncate` class,
        // because a label that cannot overflow does not need one and one that
        // could should be shortened instead.
        className={`flex items-center gap-3 rounded-full px-4 py-2 text-label transition-colors duration-150 ${
          active
            ? "bg-primary-container font-medium text-on-primary-container"
            : "text-on-surface-variant hover:bg-surface-container-high"
        }`}
      >
        <NavIcon name={link.href} />
        {link.label}
      </Link>
    );
  };

  const content = (
    // aria-label because the MOPAN line above is a <div>, so the landmark had
    // no accessible name and announced as a bare "navigation". 대화 기록 stays
    // a <div> rather than becoming a heading: the sidebar precedes <main> in
    // the DOM, so a heading here would sit above every page's <h1>.
    // No border-r. The sidebar separates from the page by tone -
    // surface-container-low against surface - which is the whole §1 principle
    // in one class. 280px per §6.
    <nav
      aria-label="주 메뉴"
      className="flex h-full w-sidebar flex-col gap-1 bg-surface-container-low p-3"
    >
      {/* §2: the gradient is allowed on the wordmark and nowhere else on this
          screen. */}
      <div className="mb-4 truncate px-4 pt-2 text-title font-medium">
        {/* 가져다 쓰는 사람의 이름이 먼저다. 브랜딩이 비어 있을 때만 MOPAN. */}
        <span className="text-gradient-brand">{branding?.app_title || "MOPAN"}</span>
      </div>
      {navLinks.map(navLink)}

      {/* `user` is null until /api/auth/me lands, so a non-admin never sees this
          appear and then vanish. flex-col on the wrapper because the links are
          <a> elements: as direct children of this flex column they stack on
          their own, but inside a plain div they would run side by side. */}
      {user?.role === "admin" && (
        <div className="mt-4">
          <div className="mb-1 px-4 text-caption tracking-wide text-on-surface-variant">관리</div>
          <div className="flex flex-col gap-1">{adminLinks.map(navLink)}</div>
        </div>
      )}

      {/* relative: 이 스크롤 영역 안의 sr-only(absolute) 요소가 뷰포트에 앵커되어
          문서를 늘리는 것을 막는다 - app-main의 같은 수정과 같은 기전. */}
      <div className="relative mt-6 flex-1 overflow-y-auto">
        <div className="mb-1 px-4 text-caption tracking-wide text-on-surface-variant">
          대화 기록
        </div>
        {error && <ErrorBanner message={error} />}
        {!error && conversations?.length === 0 && (
          <p className="px-4 py-2 text-caption text-on-surface-variant">아직 대화가 없습니다.</p>
        )}
        {/* Which conversation you are in is the one piece of state a history
            list exists to convey, and the links carried only a hover style:
            measured at /chat/c3, every link's computed background was
            rgba(0,0,0,0). Same treatment as the nav links above. */}
        {conversations?.map((c) => {
          const active = pathname === `/chat/${c.id}`;

          // The rename is an inline field in the row rather than a third
          // dialog: the row is where the name is read, and a dialog to change
          // one string would be two more focus transitions for the same edit.
          if (renamingId === c.id) {
            return (
              <form
                key={c.id}
                onSubmit={(e) => {
                  e.preventDefault();
                  void commitRename(c.id);
                }}
                className="px-1 py-1"
              >
                <input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Escape") return;
                    // stopPropagation, or the drawer's document-level Escape
                    // handler closes the whole sidebar behind the cancel.
                    e.stopPropagation();
                    cancelRenameRef.current = true;
                    setRenamingId(null);
                  }}
                  // Click-away saves. Escape is the only way to discard, and
                  // the ref is what tells the two apart if a browser fires
                  // blur while removing the focused input.
                  onBlur={() => {
                    if (cancelRenameRef.current) {
                      cancelRenameRef.current = false;
                      return;
                    }
                    void commitRename(c.id);
                  }}
                  aria-label={`대화 이름: ${c.title}`}
                  maxLength={200}
                  className="field w-full"
                />
              </form>
            );
          }

          return (
            <div
              key={c.id}
              // One blur handler for the row AND its menu: with it on the menu
              // alone, clicking the toggle to close fired blur first, closed
              // the menu, and the click then reopened it.
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setMenuFor(null);
              }}
              onKeyDown={(e) => {
                if (e.key !== "Escape" || menuFor !== c.id) return;
                e.stopPropagation();
                setMenuFor(null);
              }}
            >
              <div
                className={`flex items-center rounded-full transition-colors duration-150 ${
                  active ? "bg-primary-container" : "hover:bg-surface-container-high"
                }`}
              >
                <Link
                  href={`/chat/${c.id}`}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={`min-w-0 flex-1 truncate rounded-full px-4 py-2 text-label ${
                    active ? "font-medium text-on-primary-container" : "text-on-surface-variant"
                  }`}
                >
                  {c.title}
                </Link>
                <button
                  type="button"
                  aria-label={`대화 메뉴: ${c.title}`}
                  aria-expanded={menuFor === c.id}
                  onClick={() => setMenuFor(menuFor === c.id ? null : c.id)}
                  className="mr-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-on-surface-variant transition-colors duration-150 hover:bg-surface-container-highest"
                >
                  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                    <circle cx="12" cy="5" r="1.6" />
                    <circle cx="12" cy="12" r="1.6" />
                    <circle cx="12" cy="19" r="1.6" />
                  </svg>
                </button>
              </div>
              {menuFor === c.id && (
                // Inline, not an absolutely positioned popover: this list is
                // the sidebar's `overflow-y-auto` region, which CLIPS an
                // absolutely positioned child, so a floating menu on the last
                // visible row would be cut in half.
                <div className="my-1 flex flex-col rounded-md bg-surface-container py-1">
                  <button
                    type="button"
                    onClick={() => {
                      setMenuFor(null);
                      setRenameValue(c.title);
                      setRenamingId(c.id);
                    }}
                    className="px-4 py-2 text-left text-label text-on-surface transition-colors duration-150 hover:bg-surface-container-high"
                  >
                    이름 변경
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuFor(null);
                      setDeleteTarget(c);
                    }}
                    className="px-4 py-2 text-left text-label text-error transition-colors duration-150 hover:bg-surface-container-high"
                  >
                    삭제
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* The one surviving divider in the sidebar: it separates the account
          block from a scrolling list, where a tonal step alone would read as
          "the list continues". §1 - borders where they carry meaning. */}
      <div className="mt-3 border-t border-outline-variant pt-3">
        {logoutError && <ErrorBanner message={logoutError} />}
        {/* 계정 줄이 곧 버튼이다. 예전에는 이메일 한 줄 + 상시 로그아웃 버튼이
            바닥을 차지했는데, 로그아웃·테마·프로필은 하루 한 번도 안 만지는
            컨트롤이라 전부 계정 창(AccountMenu)으로 들어갔고 이 버튼이 그
            문이다. user가 아직 없어도 뼈대를 그려서 로딩 전후 높이가 같다 -
            예전 U+00A0 자리표시자와 같은 이유. */}
        <button
          type="button"
          onClick={() => user && setAccountOpen(true)}
          disabled={!user}
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-container"
        >
          <span
            aria-hidden="true"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-container text-label font-medium text-on-primary-container"
          >
            {user ? (user.nickname ?? user.email).slice(0, 1).toUpperCase() : "·"}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body text-on-surface">
              {user ? user.nickname || user.email : " "}
            </span>
            <span className="block truncate text-caption text-on-surface-variant">
              {user
                ? user.nickname
                  ? `${user.email}${user.role === "admin" ? " · 관리자" : ""}`
                  : user.role === "admin"
                    ? "관리자"
                    : "사용자"
                : " "}
            </span>
          </span>
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            className="h-4 w-4 shrink-0 text-on-surface-variant"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m8 9 4-4 4 4M8 15l4 4 4-4" />
          </svg>
        </button>
      </div>
    </nav>
  );

  return (
    <>
      {/* Outside `content`, which is rendered TWICE - once docked, once in the
          drawer. Inside it, one showModal() call would open two dialogs and
          only the second would be reachable. */}
      {accountOpen && user && (
        <AccountMenu
          user={user}
          onUserChange={setUser}
          onLogout={handleLogout}
          onClose={() => setAccountOpen(false)}
        />
      )}
      {deleteTarget && (
        <ConfirmDialog
          title="대화 삭제"
          message={`"${deleteTarget.title}" 대화와 그 안의 모든 메시지가 삭제됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={() => confirmDelete(deleteTarget)}
          onClose={() => setDeleteTarget(null)}
        />
      )}
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
          // 상단 예약 띠(pt-12)를 없앤 대가로 내용이 이 밑을 지나간다 - 그림자
          // 한 겹이 버튼을 지나가는 글자와 갈라 준다. (토큰 색은 alpha 변형이
          // 없어 /80 같은 투명도 수식은 조용히 무시된다 - 쓰지 말 것.)
          className="icon-btn fixed left-2 top-2 z-20 bg-surface-container text-title shadow-md md:hidden"
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
          <div className="motion-drawer relative">{content}</div>
          {/* A button, not a div: this overlay is the only way to close the
              drawer, and as a div it is unreachable without a pointer. */}
          <button
            type="button"
            aria-label="메뉴 닫기"
            className="motion-scrim flex-1 bg-scrim"
            onClick={() => setOpen(false)}
          />
        </div>
      )}
    </>
  );
}
