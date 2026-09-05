"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import DataTable from "@/components/ui/DataTable";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { ManagedUser, User } from "@/lib/types";

const ROLE_LABEL: Record<ManagedUser["role"], string> = {
  admin: "관리자",
  user: "일반",
};

export default function UsersPage() {
  const [me, setMe] = useState<User | null>(null);
  // null is "not loaded yet". GET /api/users answers a non-admin with 403
  // 관리자 권한이 필요합니다., which lands in loadError - so this page needs no
  // role branch of its own; there is nothing to render either way.
  const [users, setUsers] = useState<ManagedUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // One row acts at a time. The 409s this screen exists to show - 마지막
  // 관리자입니다..., 자신의 권한은 변경할 수 없습니다... - name the row that was
  // touched, so they render in it rather than in a banner at the top.
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [deactivateTarget, setDeactivateTarget] = useState<ManagedUser | null>(null);
  const [resetTarget, setResetTarget] = useState<ManagedUser | null>(null);
  // 방금 발급된 임시 비밀번호. 응답에 딱 한 번 실리는 값이라(서버엔 해시만
  // 남는다) 관리자가 옮겨 적을 때까지 행 밑에 붙여 둔다.
  const [tempPassword, setTempPassword] = useState<{ id: string; value: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [query, setQuery] = useState("");
  // 사용자 추가 - 공개 배포에서 자가가입을 꺼 두면 계정이 생기는 유일한 길.
  const [addOpen, setAddOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<"user" | "admin">("user");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await apiFetch<ManagedUser[]>("/api/users"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    apiFetch<User>("/api/auth/me").then(setMe).catch(() => undefined);
    void load();
  }, [load]);

  /** Applies the server's returned user, never the value that was submitted.
   * A role change that renders as done but was refused is worse than a slow
   * one: `users` is untouched on failure, so the <select> - controlled by that
   * state - snaps back to the role the backend still holds.
   *
   * `inline` is false for the call the confirmation dialog makes: it renders
   * the failure itself, and setting the row error too would print the same 409
   * twice, once of them behind the open modal. It always rethrows, which is how
   * the dialog knows to stay open. */
  async function patch(
    id: string,
    body: { role?: string; is_active?: boolean },
    inline = true,
  ) {
    setBusyId(id);
    setRowError(null);
    try {
      const updated = await apiFetch<ManagedUser>(`/api/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setUsers((prev) => (prev ?? []).map((u) => (u.id === id ? updated : u)));
    } catch (err) {
      if (inline) setRowError({ id, message: errorMessage(err) });
      throw err;
    } finally {
      setBusyId(null);
    }
  }

  async function addUser(event: React.FormEvent) {
    event.preventDefault();
    setAdding(true);
    setAddError(null);
    try {
      const created = await apiFetch<ManagedUser & { temporary_password: string }>(
        "/api/users",
        { method: "POST", body: JSON.stringify({ email: newEmail, role: newRole }) },
      );
      setCopied(false);
      setTempPassword({ id: created.id, value: created.temporary_password });
      setNewEmail("");
      setNewRole("user");
      setAddOpen(false);
      await load();
    } catch (err) {
      setAddError(errorMessage(err));
    } finally {
      setAdding(false);
    }
  }

  // 클라이언트 필터로 충분한 규모다 - 목록 전체가 이미 한 번에 온다.
  const visible = (users ?? []).filter((u) =>
    `${u.email} ${u.nickname ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <PageShell>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-headline font-medium">사용자 관리</h1>
          {users && (
            <p className="mt-1 text-caption text-on-surface-variant">
              총 {users.length}명 · 활성 {users.filter((u) => u.is_active).length}명
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {users !== null && users.length > 0 && (
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="이메일·닉네임 검색"
              aria-label="사용자 검색"
              className="field h-9 w-64 max-w-full"
            />
          )}
          <button
            type="button"
            onClick={() => {
              setAddOpen((open) => !open);
              setAddError(null);
            }}
            className="btn-filled btn-compact"
          >
            사용자 추가
          </button>
        </div>
      </div>
      <ErrorBanner message={loadError} />

      {addOpen && (
        // 공개 배포는 자가가입이 꺼져 있어(가입 화면: "회원가입이 비활성화되어
        // 있습니다") 이 폼이 계정이 생기는 유일한 길이다. 비밀번호는 안 받는다 -
        // 서버가 임시값을 만들어 한 번 보여주고, 본인이 로그인 뒤 바꾼다.
        <form
          onSubmit={(e) => void addUser(e)}
          className="mt-3 flex flex-wrap items-center gap-2 rounded-md bg-surface-container-low p-3"
        >
          <input
            type="email"
            required
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            placeholder="예) kim@example.com"
            aria-label="새 사용자 이메일"
            className="field h-9 w-64 max-w-full"
          />
          <select
            value={newRole}
            onChange={(e) => setNewRole(e.target.value as "user" | "admin")}
            aria-label="새 사용자 권한"
            className="field h-9 px-2 text-caption"
          >
            <option value="user">일반</option>
            <option value="admin">관리자</option>
          </select>
          <button type="submit" disabled={adding || !newEmail} className="btn-tonal btn-compact">
            {adding ? "추가 중..." : "임시 비밀번호로 추가"}
          </button>
          {addError && <ErrorBanner message={addError} />}
        </form>
      )}

      {users === null ? (
        !loadError && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : users.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">사용자가 없습니다.</p>
      ) : visible.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">
          &ldquo;{query}&rdquo;에 맞는 사용자가 없습니다.
        </p>
      ) : (
        <DataTable caption="등록된 사용자 목록">
            <thead>
              <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                <th scope="col" className="px-3 py-3">이메일</th>
                <th scope="col" className="px-3 py-3">권한</th>
                <th scope="col" className="px-3 py-3">상태</th>
                <th scope="col" className="px-3 py-3">가입일</th>
                <th scope="col" className="px-3 py-3">관리</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((u) => (
                <tr key={u.id} className="border-b border-outline-variant align-top">
                  <td className="px-3 py-3">
                    {u.email}
                    {/* Which row is you is what makes 자신의 권한은 변경할 수
                        없습니다. readable as an explanation instead of a riddle. */}
                    {me?.id === u.id && <span className="ml-1 text-caption text-on-surface-variant">(나)</span>}
                    {u.nickname && (
                      <p className="text-caption text-on-surface-variant">{u.nickname}</p>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <select
                      value={u.role}
                      disabled={busyId === u.id}
                      // No visible <label> per row - the column header is the
                      // label a sighted user reads, and repeating it 40 times
                      // would say nothing about WHICH user. The email does.
                      aria-label={`${u.email} 권한`}
                      onChange={(e) => {
                        void patch(u.id, { role: e.target.value }).catch(() => undefined);
                      }}
                      className="field h-8 px-2 text-caption disabled:opacity-50"
                    >
                      {Object.entries(ROLE_LABEL).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-3">
                    <span className={u.is_active ? "text-on-surface" : "text-error"}>
                      {u.is_active ? "활성" : "비활성"}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-on-surface-variant">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-3 py-3">
                    {/* 자기 행에는 없다 - 서버도 409로 거절하고, 자기 것은 계정
                        설정이 정도다. */}
                    {me?.id !== u.id && (
                      <button
                        type="button"
                        onClick={() => {
                          setRowError(null);
                          setResetTarget(u);
                        }}
                        className="btn-tonal btn-compact mr-2"
                      >
                        비밀번호 재설정
                      </button>
                    )}
                    {u.is_active ? (
                      <button
                        type="button"
                        onClick={() => {
                          setRowError(null);
                          setDeactivateTarget(u);
                        }}
                        className="btn-danger btn-compact"
                      >
                        비활성화
                      </button>
                    ) : (
                      // Reactivating takes nothing away, so it needs no
                      // confirmation step - only the deactivation does.
                      <button
                        type="button"
                        disabled={busyId === u.id}
                        onClick={() => {
                          void patch(u.id, { is_active: true }).catch(() => undefined);
                        }}
                        className="btn-tonal btn-compact"
                      >
                        활성화
                      </button>
                    )}
                    {rowError?.id === u.id && (
                      <div className="mt-2 max-w-sm">
                        <ErrorBanner message={rowError.message} />
                      </div>
                    )}
                    {tempPassword?.id === u.id && (
                      <div className="mt-2 max-w-sm rounded-md bg-surface-container p-3">
                        <p className="text-caption text-on-surface-variant">
                          임시 비밀번호입니다. 지금만 보이니 전달하고, 로그인 후 계정
                          설정에서 바로 바꾸도록 안내해 주세요.
                        </p>
                        <div className="mt-2 flex items-center gap-2">
                          <code className="rounded-sm bg-surface-container-high px-2 py-1 text-body">
                            {tempPassword.value}
                          </code>
                          <button
                            type="button"
                            onClick={() => {
                              void navigator.clipboard
                                .writeText(tempPassword.value)
                                .then(() => setCopied(true))
                                .catch(() => undefined);
                            }}
                            className="btn-tonal btn-compact"
                          >
                            {copied ? "복사됨" : "복사"}
                          </button>
                          <button
                            type="button"
                            onClick={() => setTempPassword(null)}
                            className="btn-tonal btn-compact"
                          >
                            닫기
                          </button>
                        </div>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
        </DataTable>
      )}

      {resetTarget && (
        <ConfirmDialog
          title="비밀번호 재설정"
          message={`${resetTarget.email}의 비밀번호를 임시 비밀번호로 바꿀까요? 기존 비밀번호는 즉시 무효가 되고, 사용 중인 세션도 끊깁니다.`}
          confirmLabel="재설정"
          onClose={() => setResetTarget(null)}
          onConfirm={async () => {
            const created = await apiFetch<{ temporary_password: string }>(
              `/api/users/${resetTarget.id}/password`,
              { method: "POST" },
            );
            setCopied(false);
            setTempPassword({ id: resetTarget.id, value: created.temporary_password });
          }}
        />
      )}

      {deactivateTarget && (
        <ConfirmDialog
          title="사용자 비활성화"
          message={`${deactivateTarget.email} 계정을 비활성화할까요? 로그인할 수 없게 되고, 사용 중인 세션도 즉시 끊깁니다.`}
          confirmLabel="비활성화"
          onClose={() => setDeactivateTarget(null)}
          onConfirm={() => patch(deactivateTarget.id, { is_active: false }, false)}
        />
      )}
    </PageShell>
  );
}
