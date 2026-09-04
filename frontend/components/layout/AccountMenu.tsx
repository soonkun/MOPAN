"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, errorMessage } from "@/lib/api";
import type { User } from "@/lib/types";

/** 계정 창 - 사이드바 아래 계정 줄을 누르면 뜬다.

Claude Desktop의 계정 메뉴와 같은 발상: 프로필(닉네임), 테마, 로그아웃,
계정 삭제처럼 "나에 관한 것"을 한 자리에 모은다. 예전에는 테마가 화면 오른쪽
위에 떠 있는 순환 버튼이었고 로그아웃이 사이드바 바닥의 상시 버튼이었다 -
하루 한 번도 안 누르는 컨트롤 둘이 가장 좋은 자리를 차지하고 있었다.

네이티브 <dialog> + showModal() - ConfirmDialog와 같은 이유(포커스 트랩,
Escape, inert 배경이 공짜). 사이드바 content가 도킹·드로어로 두 번 렌더되므로
이 다이얼로그는 그 밖에서 한 번만 마운트된다. md 이상에서는 계정 줄 근처
(왼쪽 아래)에 붙고, 폰에서는 가운데 - top-layer의 margin이 곧 위치다.

테마는 라이트/다크 두 단이다. 예전의 시스템/라이트/다크 순환은 "시스템"을
잃지 않으려는 설계였지만, 소유자가 두 단을 지시했다: 저장된 값이 없으면
스위치는 OS 설정을 보여주고, 누르는 순간 명시값이 저장된다. */

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
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [nickname, setNickname] = useState(user.nickname ?? "");
  const [savingNickname, setSavingNickname] = useState(false);
  const [savedNickname, setSavedNickname] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialogRef.current?.showModal();
    setTheme(currentTheme());
  }, []);

  function applyTheme(value: "light" | "dark") {
    setTheme(value);
    document.documentElement.setAttribute("data-theme", value);
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // 이 페이지에는 적용된다. 새로고침을 못 넘길 뿐이고, 아예 안 바뀌는
      // 것보다 낫다.
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

  async function deleteAccount() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/auth/me", {
        method: "DELETE",
        body: JSON.stringify({ password }),
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
      className="m-auto w-full max-w-sm rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim md:mb-20 md:ml-4 md:mr-auto md:mt-auto"
    >
      <div className="p-5">
        <div className="flex items-center gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary-container text-title font-medium text-on-primary-container"
          >
            {(user.nickname ?? user.email).slice(0, 1).toUpperCase()}
          </span>
          <div className="min-w-0">
            <h2 id="account-menu-title" className="truncate text-title font-medium">
              {user.nickname ? `${user.nickname}님` : "내 계정"}
            </h2>
            <p className="truncate text-caption text-on-surface-variant">
              {user.email}
              {user.role === "admin" ? " · 관리자" : ""}
            </p>
          </div>
        </div>

        <div className="mt-4 border-t border-outline-variant pt-4">
          <label htmlFor="account-nickname" className="text-label font-medium text-on-surface-variant">
            닉네임
          </label>
          <p className="mt-0.5 text-caption text-on-surface-variant">
            새 대화 화면과 인사가 이 이름으로 부릅니다. 비우면 부르지 않습니다.
          </p>
          <div className="mt-2 flex gap-2">
            <input
              id="account-nickname"
              value={nickname}
              onChange={(event) => setNickname(event.target.value)}
              maxLength={60}
              placeholder="예: 순쿤"
              className="field min-w-0 flex-1"
            />
            <button
              type="button"
              onClick={() => void saveNickname()}
              disabled={savingNickname || !nicknameDirty}
              className="btn-tonal btn-compact shrink-0 self-center"
            >
              {savingNickname ? "저장 중..." : savedNickname ? "저장됨" : "저장"}
            </button>
          </div>
        </div>

        <div className="mt-4 border-t border-outline-variant pt-4">
          <span className="text-label font-medium text-on-surface-variant">화면 테마</span>
          <div
            role="group"
            aria-label="화면 테마"
            className="mt-2 grid grid-cols-2 gap-1 rounded-full bg-surface-container p-1"
          >
            {(["light", "dark"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => applyTheme(value)}
                aria-pressed={theme === value}
                className={`h-8 rounded-full text-label transition-colors duration-150 ${
                  theme === value
                    ? "bg-primary-container text-on-primary-container"
                    : "text-on-surface-variant hover:bg-surface-container-high"
                }`}
              >
                {value === "light" ? "라이트" : "다크"}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 space-y-2 border-t border-outline-variant pt-4">
          <button
            type="button"
            onClick={() => void onLogout()}
            className="btn-tonal w-full"
          >
            로그아웃
          </button>
          {!deleting ? (
            <button
              type="button"
              onClick={() => setDeleting(true)}
              className="w-full rounded-sm px-2 py-2 text-label text-error hover:bg-error-container hover:text-on-error-container"
            >
              계정 삭제
            </button>
          ) : (
            <div className="rounded-md bg-error-container p-3">
              <p className="text-caption text-on-error-container">
                대화 이력이 지워지고 이 계정으로는 다시 로그인할 수 없습니다. 이 계정이 만든
                문서·분류·워크플로우는 시스템에 남습니다. 되돌릴 수 없습니다.
              </p>
              <label htmlFor="account-delete-password" className="sr-only">
                비밀번호 확인
              </label>
              <input
                id="account-delete-password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="비밀번호를 다시 입력하세요"
                className="field mt-2 w-full"
              />
              <div className="mt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setDeleting(false);
                    setPassword("");
                    setError(null);
                  }}
                  className="btn-tonal btn-compact"
                >
                  취소
                </button>
                <button
                  type="button"
                  onClick={() => void deleteAccount()}
                  disabled={busy || !password}
                  className="btn-danger btn-compact"
                >
                  {busy ? "삭제 중..." : "계정 삭제"}
                </button>
              </div>
            </div>
          )}
        </div>

        {error && (
          <p className="mt-3 rounded-sm bg-error-container px-3 py-2 text-caption text-on-error-container">
            {error}
          </p>
        )}

        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="btn-text btn-compact"
          >
            닫기
          </button>
        </div>
      </div>
    </dialog>
  );
}
