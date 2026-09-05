"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import Switch from "@/components/ui/Switch";
import type { User } from "@/lib/types";

/** 계정 창 - 사이드바 아래 계정 줄을 누르면 뜬다. 클로드 데스크톱의 계정
팝오버가 모양의 기준이다.

컴팩트하다: 좁은 창(18rem), 설명 문장 없음. 위에서부터 프로필 헤더 → 닉네임
→ 테마 스위치 행(아이콘은 지금 상태 - 라이트면 해) → 계정 설정 행 → 로그아웃
행. 계정 삭제는 첫 화면에 두지 않는다 - "계정 설정" 안으로 한 번 더 들어가야
맨 아래 나온다. 파괴적 동작이 클릭 한 번 거리면 그것은 기능이 아니라 함정이다.

네이티브 <dialog> + showModal() (ConfirmDialog와 같은 이유). 창 밖(backdrop)을
클릭하면 닫힌다 - 닫기 버튼은 없다: dialog 요소 자체가 클릭 대상이면 그것이
backdrop이다(내용은 안쪽 div가 전부 덮는다). 사이드바 content가 도킹·드로어로
두 번 렌더되므로 이 다이얼로그는 그 밖에서 한 번만 마운트된다.

테마는 라이트/다크 두 단: 저장된 값이 없으면 OS 설정을 보여주고, 누르는 순간
명시값이 저장된다. */

const STORAGE_KEY = "mopan-theme";

function currentTheme(): "light" | "dark" {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // 프라이빗 모드 - 아래 OS 값이 답이다.
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function IconGlyph({ name }: { name: "sun" | "moon" | "logout" | "back" | "chevron" | "gear" }) {
  const paths = {
    sun: (
      <>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </>
    ),
    moon: <path d="M20 13.5A8 8 0 1 1 10.5 4 6.5 6.5 0 0 0 20 13.5Z" />,
    logout: (
      <>
        <path d="M15 12H4" />
        <path d="m7.5 8.5-3.5 3.5 3.5 3.5" />
        <path d="M11 5h6a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-6" />
      </>
    ),
    back: <path d="m14 6-6 6 6 6" />,
    chevron: <path d="m10 6 6 6-6 6" />,
    // 슬라이더 - 설정. 톱니보다 획이 적고 이 크기에서 더 또렷하다.
    gear: (
      <>
        <path d="M4 7h8M18 7h2M4 17h4M14 17h6" />
        <circle cx="15" cy="7" r="2.2" />
        <circle cx="11" cy="17" r="2.2" />
      </>
    ),
  } as const;
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-5 w-5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}

export default function AccountMenu({
  user,
  onUserChange,
  onLogout,
  onClose,
}: {
  user: User;
  onUserChange: (next: User) => void;
  onLogout: () => Promise<void>;
  onClose: () => void;
}) {
  const router = useRouter();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [view, setView] = useState<"main" | "settings">("main");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [nickname, setNickname] = useState(user.nickname ?? "");
  const [savingNickname, setSavingNickname] = useState(false);
  const [savedNickname, setSavedNickname] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 비밀번호 변경.
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordAgain, setNewPasswordAgain] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordChanged, setPasswordChanged] = useState(false);

  // 계정 삭제 (계정 설정 맨 아래에서만).
  const [deleting, setDeleting] = useState(false);
  const [deletePassword, setDeletePassword] = useState("");
  const [busy, setBusy] = useState(false);

  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dialogRef.current?.showModal();
    // showModal은 첫 포커스 가능한 요소 - 닉네임 입력칸 - 에 포커스를 준다.
    // 아이폰에서는 그 즉시 키보드가 올라오고 화면이 줌인됐다(실사고): 바꿀
    // 생각도 없는 칸에 커서부터 꽂는 것이다. 패널 자체(tabIndex -1)로 옮겨
    // 키보드도 줌도 없이 열리고, Tab 한 번이면 닉네임에 닿는다.
    panelRef.current?.focus();
    setTheme(currentTheme());
  }, []);

  function toggleTheme() {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 이 페이지에는 적용된다. 새로고침을 못 넘길 뿐.
    }
  }

  async function saveNickname() {
    setSavingNickname(true);
    setError(null);
    try {
      const updated = await apiFetch<User>("/api/auth/me", {
        method: "PATCH",
        body: JSON.stringify({ nickname }),
      });
      onUserChange(updated);
      setNickname(updated.nickname ?? "");
      setSavedNickname(true);
      setTimeout(() => setSavedNickname(false), 2000);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSavingNickname(false);
    }
  }

  async function changePassword() {
    if (newPassword !== newPasswordAgain) {
      setError("새 비밀번호가 서로 다릅니다.");
      return;
    }
    setChangingPassword(true);
    setError(null);
    try {
      await apiFetch("/api/auth/me/password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      setCurrentPassword("");
      setNewPassword("");
      setNewPasswordAgain("");
      setPasswordChanged(true);
      setTimeout(() => setPasswordChanged(false), 2500);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setChangingPassword(false);
    }
  }

  async function deleteAccount() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/auth/me", {
        method: "DELETE",
        body: JSON.stringify({ password: deletePassword }),
      });
      router.push("/login");
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  const nicknameDirty = nickname.trim() !== (user.nickname ?? "");

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="account-menu-title"
      onClose={onClose}
      // 창 밖 클릭 = 닫기. 내용은 안쪽 div가 전부 덮으므로, 클릭 대상이
      // dialog 자신이면 그것은 backdrop이다.
      onClick={(event) => {
        if (event.target === dialogRef.current) dialogRef.current?.close();
      }}
      className="m-auto w-[calc(100vw-2rem)] max-w-[18rem] rounded-lg border border-outline-variant bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim md:mb-20 md:ml-4 md:mr-auto md:mt-auto"
    >
      <div ref={panelRef} tabIndex={-1} className="p-2 focus:outline-none">
        {view === "main" ? (
          <>
            <div className="flex items-center gap-3 px-2 pb-2 pt-2">
              <span
                aria-hidden="true"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary-container text-body font-medium text-on-primary-container"
              >
                {(user.nickname ?? user.email).slice(0, 1).toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                <h2 id="account-menu-title" className="truncate text-body font-medium">
                  {user.nickname ? `${user.nickname}님` : "내 계정"}
                </h2>
                <p className="truncate text-caption text-on-surface-variant">
                  {user.email}
                  {user.role === "admin" ? " · 관리자" : ""}
                </p>
              </div>
            </div>

            <div className="border-t border-outline-variant px-2 py-2">
              <div className="flex gap-2">
                <input
                  id="account-nickname"
                  value={nickname}
                  onChange={(event) => setNickname(event.target.value)}
                  maxLength={60}
                  placeholder="닉네임"
                  aria-label="닉네임"
                  className="field h-9 min-w-0 flex-1 text-body"
                />
                {/* 저장 버튼은 바꿨을 때만 나타난다 - 안 바꾼 창에 버튼이 있는
                    것이 곧 "묻힌" 인상의 절반이었다. */}
                {nicknameDirty && (
                  <button
                    type="button"
                    onClick={() => void saveNickname()}
                    disabled={savingNickname}
                    className="btn-tonal btn-compact shrink-0 self-center"
                  >
                    {savingNickname ? "저장 중..." : "저장"}
                  </button>
                )}
                {savedNickname && !nicknameDirty && (
                  <span className="self-center text-caption text-primary">저장됨</span>
                )}
              </div>
            </div>

            {/* 메뉴 행들. 테마 행의 아이콘은 지금 상태다: 라이트면 해. */}
            <div className="border-t border-outline-variant pt-1">
              <button
                type="button"
                onClick={toggleTheme}
                aria-pressed={theme === "dark"}
                className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-body hover:bg-surface-container-high"
              >
                <span className="text-on-surface-variant">
                  <IconGlyph name={theme === "light" ? "sun" : "moon"} />
                </span>
                <span className="min-w-0 flex-1">다크 모드</span>
                <Switch on={theme === "dark"} />
              </button>
              <button
                type="button"
                onClick={() => {
                  setView("settings");
                  setError(null);
                }}
                className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-body hover:bg-surface-container-high"
              >
                <span className="text-on-surface-variant">
                  <IconGlyph name="gear" />
                </span>
                <span className="min-w-0 flex-1">계정 설정</span>
                <span className="text-on-surface-variant">
                  <IconGlyph name="chevron" />
                </span>
              </button>
            </div>

            <div className="mt-1 border-t border-outline-variant pt-1">
              <button
                type="button"
                onClick={() => void onLogout()}
                className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-body hover:bg-surface-container-high"
              >
                <span className="text-on-surface-variant">
                  <IconGlyph name="logout" />
                </span>
                로그아웃
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="flex items-center gap-2 px-1 pt-1">
              <button
                type="button"
                onClick={() => {
                  setView("main");
                  setDeleting(false);
                  setDeletePassword("");
                  setError(null);
                }}
                aria-label="뒤로"
                className="icon-btn h-8 w-8 shrink-0"
              >
                <IconGlyph name="back" />
              </button>
              <h2 id="account-menu-title" className="text-body font-medium">
                계정 설정
              </h2>
            </div>

            <div className="mt-2 border-t border-outline-variant px-2 pt-3">
              <span className="text-label font-medium text-on-surface-variant">비밀번호 변경</span>
              <div className="mt-2 space-y-2">
                <input
                  type="password"
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                  placeholder="현재 비밀번호"
                  autoComplete="current-password"
                  className="field w-full"
                  aria-label="현재 비밀번호"
                />
                <input
                  type="password"
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  placeholder="새 비밀번호"
                  autoComplete="new-password"
                  className="field w-full"
                  aria-label="새 비밀번호"
                />
                <input
                  type="password"
                  value={newPasswordAgain}
                  onChange={(event) => setNewPasswordAgain(event.target.value)}
                  placeholder="새 비밀번호 확인"
                  autoComplete="new-password"
                  className="field w-full"
                  aria-label="새 비밀번호 확인"
                />
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void changePassword()}
                    disabled={changingPassword || !currentPassword || !newPassword}
                    className="btn-tonal btn-compact"
                  >
                    {changingPassword ? "변경 중..." : "변경"}
                  </button>
                  {passwordChanged && (
                    <span className="text-caption text-primary">변경됐습니다.</span>
                  )}
                </div>
              </div>
            </div>

            {/* 파괴적 동작은 맨 아래, 한 겹 더 안쪽에. */}
            <div className="mt-3 border-t border-outline-variant px-2 pb-1 pt-2">
              {!deleting ? (
                <button
                  type="button"
                  onClick={() => setDeleting(true)}
                  className="w-full rounded-sm px-2 py-2 text-left text-label text-error hover:bg-error-container hover:text-on-error-container"
                >
                  계정 삭제
                </button>
              ) : (
                <div className="rounded-md bg-error-container p-3">
                  <p className="text-caption text-on-error-container">
                    대화 이력이 지워지고 이 계정으로는 다시 로그인할 수 없습니다. 이 계정이 만든
                    문서·분류·워크플로우는 시스템에 남습니다. 되돌릴 수 없습니다.
                  </p>
                  <input
                    type="password"
                    value={deletePassword}
                    onChange={(event) => setDeletePassword(event.target.value)}
                    placeholder="비밀번호를 다시 입력하세요"
                    aria-label="비밀번호 확인"
                    className="field mt-2 w-full"
                  />
                  <div className="mt-2 flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setDeleting(false);
                        setDeletePassword("");
                        setError(null);
                      }}
                      className="btn-tonal btn-compact"
                    >
                      취소
                    </button>
                    <button
                      type="button"
                      onClick={() => void deleteAccount()}
                      disabled={busy || !deletePassword}
                      className="btn-danger btn-compact"
                    >
                      {busy ? "삭제 중..." : "계정 삭제"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </>
        )}

        {error && (
          <p className="mx-2 mb-1 mt-3 rounded-sm bg-error-container px-3 py-2 text-caption text-on-error-container">
            {error}
          </p>
        )}
      </div>
    </dialog>
  );
}
