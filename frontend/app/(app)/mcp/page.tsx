"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import PageShell from "@/components/layout/PageShell";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import DataTable from "@/components/ui/DataTable";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { McpRiskLevel, McpServer, McpTool } from "@/lib/types";

const RISK_LABEL: Record<McpRiskLevel, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};
const RISK_ORDER: McpRiskLevel[] = ["read", "write", "destructive"];

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

export default function McpPage() {
  // null is "not loaded yet", which is not an empty list - the same distinction
  // the 분류, 사용자 and 프롬프트 screens draw. Every endpoint behind this page
  // answers a non-admin with 403 관리자 권한이 필요합니다., which lands in
  // loadError, so there is no role branch here.
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [expanded, setExpanded] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<McpServer | null>(null);

  const load = useCallback(async () => {
    try {
      setServers(await apiFetch<McpServer[]>("/api/mcp/servers"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      // The token is sent once and never comes back. `auth_kind` is derived from
      // whether one was typed rather than being a third field the admin has to
      // keep consistent with it.
      const created = await apiFetch<McpServer>("/api/mcp/servers", {
        method: "POST",
        body: JSON.stringify({
          name,
          base_url: baseUrl,
          auth_kind: token.trim() ? "bearer" : "none",
          auth_token: token.trim() || null,
        }),
      });
      setName("");
      setBaseUrl("");
      setToken("");
      await load();
      setExpanded(created.id);
      // The row exists even when discovery failed - a mistyped port is worth
      // correcting rather than re-registering - so the failure is reported here
      // rather than swallowed by a successful create.
      if (created.discovery_error) {
        setRowError({ id: created.id, message: created.discovery_error });
      }
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  async function act(id: string, run: () => Promise<unknown>) {
    setBusyId(id);
    setRowError(null);
    try {
      await run();
      await load();
    } catch (err) {
      setRowError({ id, message: errorMessage(err) });
    } finally {
      setBusyId(null);
    }
  }

  async function rediscover(server: McpServer) {
    await act(server.id, async () => {
      const updated = await apiFetch<McpServer>(`/api/mcp/servers/${server.id}/discover`, {
        method: "POST",
      });
      if (updated.discovery_error) throw new Error(updated.discovery_error);
    });
  }

  function updateTool(server: McpServer, tool: McpTool, body: Partial<McpTool>) {
    return act(server.id, () =>
      apiFetch(`/api/mcp/tools/${tool.id}`, { method: "PATCH", body: JSON.stringify(body) }),
    );
  }

  return (
    <PageShell>
      <h1 className="text-headline font-medium">MCP 서버 관리</h1>
      <ErrorBanner message={loadError} />

      <form onSubmit={handleCreate} className="space-y-3 rounded-md bg-surface-container-low p-6">
        <h2 className="text-title font-medium">서버 등록</h2>

        {/* Not an ErrorBanner - nothing has gone wrong - so a .notice per §1
            and §4: tone, not a rule. Every bullet is something the admin has to
            know BEFORE typing a token. */}
        <div className="notice">
          <ul className="list-disc space-y-1 pl-5 text-on-surface-variant">
            <li>
              HTTP(S) 방식의 MCP 서버만 등록할 수 있습니다. 내부망·루프백 주소는 보안상 거부됩니다.
            </li>
            <li>
              인증 토큰은 저장 후 다시 볼 수 없으며, <strong>암호화되지 않은 상태로 저장</strong>
              됩니다. 데이터베이스에 접근할 수 있는 사람은 토큰을 볼 수 있습니다.
            </li>
            <li>
              등록하면 곧바로 도구 목록을 가져옵니다. 새로 발견한 도구의 위험도는 항상 &quot;쓰기&quot;로
              시작하며, 필요하면 아래 표에서 직접 바꿔 주세요.
            </li>
          </ul>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <label htmlFor="mcp-name" className="text-label font-medium text-on-surface-variant">
              이름
            </label>
            <input
              id="mcp-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={200}
              placeholder="예) 사내 지식 서버"
              className="field mt-1 w-full"
            />
          </div>
          <div>
            <label htmlFor="mcp-url" className="text-label font-medium text-on-surface-variant">
              주소
            </label>
            <input
              id="mcp-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              required
              maxLength={1000}
              placeholder="예) https://example.com/mcp"
              className="field mt-1 w-full"
            />
          </div>
          <div>
            <label htmlFor="mcp-token" className="text-label font-medium text-on-surface-variant">
              인증 토큰 (선택)
            </label>
            <input
              id="mcp-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              maxLength={4000}
              autoComplete="off"
              className="field mt-1 w-full"
            />
          </div>
        </div>

        <ErrorBanner message={createError} />
        <div className="flex justify-end">
          <button type="submit" disabled={creating} className="btn-filled">
            {creating ? "등록 중..." : "등록하고 도구 가져오기"}
          </button>
        </div>
      </form>

      {servers === null ? (
        !loadError && (
          <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
        )
      ) : servers.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">
          등록된 MCP 서버가 없습니다.
        </p>
      ) : (
        <DataTable caption="등록된 MCP 서버 목록">
            <thead>
              <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                <th scope="col" className="px-3 py-3">서버</th>
                <th scope="col" className="px-3 py-3">인증</th>
                <th scope="col" className="px-3 py-3">상태</th>
                <th scope="col" className="px-3 py-3">도구</th>
                <th scope="col" className="px-3 py-3">관리</th>
              </tr>
            </thead>
            <tbody>
              {servers.map((server) => (
                <Fragment key={server.id}>
                  <tr className="border-b border-outline-variant align-top">
                    <td className="px-3 py-3">
                      <div className="font-medium">{server.name}</div>
                      <div className="break-all text-caption text-on-surface-variant">
                        {server.base_url}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      {/* Whether a token is set, never the token: the API has no
                          field that could carry it back. */}
                      {server.has_auth_token ? "토큰 있음" : "없음"}
                    </td>
                    <td className="px-3 py-3">
                      {server.enabled ? (
                        <span className="text-primary">사용 중</span>
                      ) : (
                        <span className="text-on-surface-variant">중지</span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      {server.tools.filter((t) => t.enabled).length}/{server.tools.length}개
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          aria-expanded={expanded === server.id}
                          aria-controls={`mcp-tools-${server.id}`}
                          onClick={() => setExpanded(expanded === server.id ? null : server.id)}
                          className="btn-tonal btn-compact"
                        >
                          {expanded === server.id ? "도구 접기" : "도구 보기"}
                        </button>
                        <button
                          type="button"
                          disabled={busyId === server.id}
                          onClick={() => void rediscover(server)}
                          className="btn-tonal btn-compact"
                        >
                          도구 다시 가져오기
                        </button>
                        <button
                          type="button"
                          disabled={busyId === server.id}
                          onClick={() =>
                            void act(server.id, () =>
                              apiFetch(`/api/mcp/servers/${server.id}`, {
                                method: "PATCH",
                                body: JSON.stringify({ enabled: !server.enabled }),
                              }),
                            )
                          }
                          className="btn-tonal btn-compact"
                        >
                          {server.enabled ? "중지" : "사용"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(server)}
                          className="btn-danger btn-compact"
                        >
                          삭제
                        </button>
                      </div>
                      {rowError?.id === server.id && <ErrorBanner message={rowError.message} />}
                      {server.discovery_error && rowError?.id !== server.id && (
                        <ErrorBanner message={server.discovery_error} />
                      )}
                    </td>
                  </tr>
                  {expanded === server.id && (
                    <tr className="border-b border-outline-variant">
                      <td colSpan={5} className="px-3 pb-4">
                        <div id={`mcp-tools-${server.id}`}>
                          {server.tools.length === 0 ? (
                            <p className="py-3 text-body text-on-surface-variant">
                              아직 가져온 도구가 없습니다.
                            </p>
                          ) : (
                            <table className="w-full text-left text-body">
                              <caption className="sr-only">{server.name}의 도구 목록</caption>
                              <thead>
                                <tr className="text-label font-medium text-on-surface-variant">
                                  <th scope="col" className="py-2 pr-3">도구</th>
                                  <th scope="col" className="py-2 pr-3">위험도</th>
                                  <th scope="col" className="py-2 pr-3">상태</th>
                                  <th scope="col" className="py-2 pr-3">확인 시각</th>
                                  <th scope="col" className="py-2">사용</th>
                                </tr>
                              </thead>
                              <tbody>
                                {server.tools.map((tool) => (
                                  <tr key={tool.id} className="border-t border-outline-variant align-top">
                                    <td className="py-2 pr-3">
                                      <div className="font-medium">{tool.name}</div>
                                      {tool.description && (
                                        <div className="text-caption text-on-surface-variant">
                                          {tool.description}
                                        </div>
                                      )}
                                    </td>
                                    <td className="py-2 pr-3">
                                      <label className="sr-only" htmlFor={`risk-${tool.id}`}>
                                        {tool.name} 위험도
                                      </label>
                                      <select
                                        id={`risk-${tool.id}`}
                                        value={tool.risk_level}
                                        disabled={busyId === server.id}
                                        onChange={(e) =>
                                          void updateTool(server, tool, {
                                            risk_level: e.target.value as McpRiskLevel,
                                          })
                                        }
                                        className="field h-8 py-0 text-caption"
                                      >
                                        {RISK_ORDER.map((level) => (
                                          <option key={level} value={level}>
                                            {RISK_LABEL[level]}
                                          </option>
                                        ))}
                                      </select>
                                    </td>
                                    <td className="py-2 pr-3">
                                      {tool.enabled ? (
                                        <span className="text-primary">사용 중</span>
                                      ) : (
                                        <span className="text-on-surface-variant">중지</span>
                                      )}
                                    </td>
                                    <td className="py-2 pr-3 text-caption text-on-surface-variant">
                                      {formatDate(tool.discovered_at)}
                                    </td>
                                    <td className="py-2">
                                      <button
                                        type="button"
                                        disabled={busyId === server.id}
                                        onClick={() =>
                                          void updateTool(server, tool, { enabled: !tool.enabled })
                                        }
                                        className="btn-tonal btn-compact"
                                      >
                                        {tool.enabled ? "중지" : "사용"}
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                          <p className="mt-3 text-caption text-on-surface-variant">
                            서버가 더 이상 제공하지 않는 도구는 삭제하지 않고 &quot;중지&quot;로
                            표시합니다. 이미 남아 있는 답변이 그 도구를 인용하고 있기 때문입니다.
                          </p>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
        </DataTable>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="MCP 서버 삭제"
          message={`"${deleteTarget.name}" 서버와 그 서버에서 가져온 도구가 모두 삭제됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/mcp/servers/${deleteTarget.id}`, { method: "DELETE" });
            await load();
          }}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </PageShell>
  );
}
