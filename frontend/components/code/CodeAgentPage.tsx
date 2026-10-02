"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import {
  EMPTY_THREAD,
  TOOL_LABEL,
  applyEvent,
  ocPath,
  toolSummary,
  type CodeStatus,
  type FileDiff,
  type OcMessage,
  type OcSession,
  type Permission,
  type ThreadState,
  type Workspace,
  openOcEvents,
} from "@/lib/code";
import CodeComposer from "@/components/code/CodeComposer";
import ConnectDialog from "@/components/code/ConnectDialog";
import FilesPanel from "@/components/code/FilesPanel";
import { PermissionCard, QuestionCard } from "@/components/code/PermissionCard";
import Thread from "@/components/code/Thread";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { AnswerModel } from "@/lib/types";

/** 코워크·코드 - 한 화면, 두 입구. 계약: docs/superpowers/plans/2026-09-24-code-agent.md §3.6
 *
 * 코워크(/cowork): 설치 없음, 서버 작업 공간만, 자동 진행, 과정 접힘 - 자료를 넣고 결과 파일을 받는 사람.
 * 코드(/code): 내 컴퓨터 연결까지, 매번 확인, 도구 호출 전부 - 코딩하는 사람. 작업 공간(서버 폴더)은 둘이 공유한다.
 *
 * 사용자 관점의 약속 셋: (1) 들어오면 바로 칠 수 있다 - 작업 공간이 없으면 '내 작업'을 만들어 두고 입력창은
 * 항상 떠 있다. (2) 무엇을 하는지 보인다 - 도구 카드가 순서대로 쌓이고, 지금 도는 도구가 입력창 옆에 뜬다.
 * (3) 결과물을 손에 쥘 수 있다 - 답 밑의 '변경 파일'과 오른쪽 '파일' 패널에서 파일 하나든 zip이든 받는다.
 *
 * 화면은 OpenCode API를 프록시(`/api/code/ws/{ws}/oc/*`)로 그대로 부르고, 이벤트 스트림을 `applyEvent`로 접어
 * 그릴 뿐이다. 세션·메시지·권한은 전부 그쪽의 것이다. */

const DEFAULT_WORKSPACE = "내 작업";
export type Mode = "simple" | "dev";

const COPY: Record<Mode, { title: string; wsKey: string; suggestions: string[] }> = {
  simple: {
    title: "코워크",
    wsKey: "cowork.ws",
    suggestions: [
      "첨부한 엑셀 파일 여러 개를 하나로 합치고 중복 행을 정리해 줘",
      "이 CSV의 요약 통계와 그래프를 만들어 보고서용 이미지로 저장해 줘",
      "첨부한 회의록을 항목별로 정리한 문서(docx)로 만들어 줘",
      "폴더 안 PDF들에서 표를 뽑아 엑셀 한 장으로 모아 줘",
    ],
  },
  dev: {
    title: "코드",
    wsKey: "code.ws",
    suggestions: [
      "이 폴더의 코드 구조를 설명해 줘",
      "실패하는 테스트를 찾아서 고쳐 줘",
      "CSV 파일을 읽어 요약 통계와 그래프를 만드는 파이썬 스크립트를 작성해 줘",
      "이 저장소에 README와 실행 스크립트를 추가해 줘",
    ],
  },
};

export default function CodeAgentPage({ mode }: { mode: Mode }) {
  const copy = COPY[mode];
  const simple = mode === "simple";
  const [status, setStatus] = useState<CodeStatus | null>(null);
  const [models, setModels] = useState<AnswerModel[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [wsId, setWsId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<OcSession[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [thread, setThread] = useState<ThreadState>(EMPTY_THREAD);
  const [diff, setDiff] = useState<FileDiff[]>([]);
  const [model, setModel] = useState("");
  const [autoApprove, setAutoApprove] = useState(mode === "simple");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<null | "new" | "connect">(null);
  const [deleteWs, setDeleteWs] = useState<Workspace | null>(null);
  const [railOpen, setRailOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [filesKey, setFilesKey] = useState(0);
  const [newName, setNewName] = useState("");
  const [uploading, setUploading] = useState(false);
  // 이벤트 스트림 콜백은 마운트 시점의 클로저라 현재 세션을 ref로 본다.
  const sessionRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const autoCreated = useRef(false);
  const headerRef = useRef<HTMLElement>(null);

  // 사이드바 햄버거(fixed, top: var(--hamburger-top))를 이 머리 줄의 세로 중심에 맞춘다. 추측한 픽셀이 아니라
  // 실제 렌더된 줄의 위치를 재서 변수로 넘긴다(워크플로우 편집기와 같은 방식) - 화면을 떠나면 되돌린다.
  useLayoutEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const root = document.documentElement;
    const align = () => {
      const r = el.getBoundingClientRect();
      root.style.setProperty("--hamburger-top", `${Math.round(r.top + (r.height - 40) / 2)}px`);
    };
    align();
    const ro = new ResizeObserver(align);
    ro.observe(el);
    window.addEventListener("resize", align);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", align);
      root.style.removeProperty("--hamburger-top");
    };
  }, []);
  // 코워크: 첫 요청이 작업 폴더+대화를 만든다. wsId 효과가 그 대화를 열도록 미리 적어 둔다.
  const pendingSessionRef = useRef<string | null>(null);

  const ws = workspaces?.find((w) => w.id === wsId) ?? null;

  const loadStatus = useCallback(async () => {
    try {
      const s = await apiFetch<CodeStatus>("/api/code/status");
      setStatus(s);
      apiFetch<AnswerModel[]>("/api/models").then(setModels).catch(() => {});
      setModel((m) => {
        let remembered: string | null = null;
        try {
          remembered = localStorage.getItem("code.model");
        } catch {
          /* ignore */
        }
        const ids = s.models.map((x) => x.id);
        if (m && ids.includes(m)) return m;
        if (remembered && ids.includes(remembered)) return remembered;
        return s.default_model && ids.includes(s.default_model) ? s.default_model : ids[0] ?? "";
      });
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  const loadWorkspaces = useCallback(async () => {
    try {
      if (simple) {
        // 코워크: 작업(대화)마다 폴더 하나. 들어오면 새 작업(초안)에서 시작하고, 지난 작업은 목록에서 고른다.
        const tasks = await apiFetch<{ id: string; name: string; updated_at: number; files: number }[]>("/api/code/cowork/tasks");
        const list: Workspace[] = tasks.map((t) => ({ id: t.id, kind: "cowork", name: t.name, path: "", online: true, files: t.files, updated_at: t.updated_at }));
        setWorkspaces(list);
        setWsId((cur) => (cur && list.some((w) => w.id === cur) ? cur : null));
        return;
      }
      const list = await apiFetch<Workspace[]>("/api/code/workspaces");
      setWorkspaces(list);
      setWsId((cur) => {
        if (cur && list.some((w) => w.id === cur)) return cur;
        let remembered: string | null = null;
        try {
          remembered = localStorage.getItem(copy.wsKey);
        } catch {
          /* ignore */
        }
        return list.find((w) => w.id === remembered)?.id ?? list[0]?.id ?? null;
      });
    } catch (e) {
      setError(errorMessage(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simple, copy.wsKey]);

  useEffect(() => {
    void loadStatus();
    void loadWorkspaces();
    // 내 컴퓨터 연결은 언제든 붙고 떨어지므로 목록·상태를 주기적으로 다시 읽는다.
    const t = setInterval(() => {
      void loadStatus();
      void loadWorkspaces();
    }, 15000);
    return () => clearInterval(t);
  }, [loadStatus, loadWorkspaces]);

  // 첫 방문: 작업 공간이 하나도 없으면 '내 작업'을 만들어 둔다 - 입력창이 뜨지 않는 코드 탭은 코드 탭이 아니다.
  useEffect(() => {
    if (simple || !status?.server_available || workspaces === null || workspaces.length > 0 || autoCreated.current) return;
    autoCreated.current = true;
    apiFetch<Workspace>("/api/code/workspaces", { method: "POST", body: JSON.stringify({ name: DEFAULT_WORKSPACE }) })
      .then(() => loadWorkspaces())
      .catch((e) => setError(errorMessage(e)));
  }, [status, workspaces, loadWorkspaces, simple]);

  useEffect(() => {
    if (!model) return;
    try {
      localStorage.setItem("code.model", model);
    } catch {
      /* ignore */
    }
  }, [model]);

  const loadSessions = useCallback(async (id: string) => {
    try {
      const list = await apiFetch<OcSession[]>(ocPath(id, "session"));
      const roots = list.filter((s) => !("parentID" in s && (s as { parentID?: string }).parentID)).sort((a, b) => b.time.updated - a.time.updated);
      setSessions(roots);
      setError(null);
      return roots;
    } catch (e) {
      setError(errorMessage(e));
      return [];
    }
  }, []);

  const loadDiff = useCallback(async (id: string, sid: string) => {
    try {
      setDiff(await apiFetch<FileDiff[]>(ocPath(id, `session/${sid}/diff`)));
    } catch {
      setDiff([]);
    }
  }, []);

  const openSession = useCallback(
    async (id: string, sid: string | null) => {
      // 이미 보고 있는 대화를 다시 열 때(새 작업 첫 요청: send가 낙관적으로 먼저 그려 둔다)는 비우지 않는다 -
      // 비우고 다시 채우는 한 프레임이 "답변 전 깜빡임"이었다(소유자 지적 2026-09-27).
      const same = sid !== null && sid === sessionRef.current;
      sessionRef.current = sid;
      setSessionId(sid);
      if (!same) {
        setThread(EMPTY_THREAD);
        setDiff([]);
      }
      if (!sid) return;
      try {
        const [messages, perms] = await Promise.all([
          apiFetch<OcMessage[]>(ocPath(id, `session/${sid}/message`)),
          apiFetch<Permission[]>(ocPath(id, "permission")).catch(() => [] as Permission[]),
        ]);
        if (sessionRef.current !== sid) return;
        // 서버 목록이 아직 낙관적 메시지를 모르면(요청 직후) 그려 둔 것을 지우지 않는다.
        setThread((prev) => ({
          ...prev,
          messages: messages.length >= prev.messages.length ? messages : prev.messages,
          permissions: perms.filter((p) => p.sessionID === sid),
          error: null,
        }));
        void loadDiff(id, sid);
      } catch (e) {
        setError(errorMessage(e));
      }
    },
    [loadDiff],
  );

  // 작업 공간이 바뀌면: 기억하고, 세션 목록을 읽고, 이벤트 스트림을 연다.
  useEffect(() => {
    if (!wsId) return;
    try {
      localStorage.setItem(copy.wsKey, wsId);
    } catch {
      /* ignore */
    }
    const pending = pendingSessionRef.current;
    pendingSessionRef.current = null;
    void openSession(wsId, pending);
    void loadSessions(wsId).then((roots) => {
      // 코워크에서 지난 작업을 고르면 그 작업의 마지막 대화를 바로 편다 - 작업 = 대화 하나가 보통이다.
      if (simple && !pending && !sessionRef.current && roots.length > 0) void openSession(wsId, roots[0].id);
    });
    const close = openOcEvents(wsId, (evt) => {
      if (evt.type === "session.created" || evt.type === "session.updated" || evt.type === "session.deleted") void loadSessions(wsId);
      if (evt.type === "file.edited") setFilesKey((k) => k + 1);
      const cur = sessionRef.current;
      if (!cur) return;
      setThread((prev) => applyEvent(prev, evt, cur));
      if (evt.type === "session.idle" && evt.properties?.sessionID === cur) {
        void loadDiff(wsId, cur);
        setFilesKey((k) => k + 1);
      }
    });
    return close;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsId, loadSessions, openSession, loadDiff]);

  // 자동 승인: 화면이 대신 "이번만"으로 답한다. 실행 중인 명령을 하나씩 보고 싶으면 끈다.
  useEffect(() => {
    if (!autoApprove || !wsId) return;
    for (const p of thread.permissions) void reply(p, "once");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoApprove, thread.permissions, wsId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [thread.messages, thread.permissions, thread.busy]);

  async function reply(p: Permission, r: "once" | "always" | "reject") {
    if (!wsId) return;
    setThread((prev) => ({ ...prev, permissions: prev.permissions.filter((x) => x.id !== p.id) }));
    try {
      await apiFetch(ocPath(wsId, `permission/${p.id}/reply`), { method: "POST", body: JSON.stringify({ reply: r }) });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  /** 코워크의 새 작업 폴더. 이름은 첫 요청의 앞부분(서버가 다듬는다). */
  async function ensureTask(name: string): Promise<string> {
    const t = await apiFetch<{ id: string; name: string }>("/api/code/cowork/tasks", { method: "POST", body: JSON.stringify({ name }) });
    setWorkspaces((prev) => [{ id: t.id, kind: "cowork", name: t.name, path: "", online: true }, ...(prev ?? [])]);
    return t.id;
  }

  async function send(promptText?: string) {
    const prompt = (promptText ?? text).trim();
    if (!prompt || !model || thread.busy) return;
    if (!wsId && !simple) return;
    setError(null);
    try {
      const id = wsId ?? (await ensureTask(prompt.replace(/\s+/g, " ").slice(0, 40)));
      let sid = sessionId;
      if (!sid || id !== wsId) {
        const s = await apiFetch<OcSession>(ocPath(id, "session"), { method: "POST", body: JSON.stringify({}) });
        sid = s.id;
        if (id === wsId) {
          sessionRef.current = sid;
          setSessionId(sid);
          setThread(EMPTY_THREAD);
        }
      }
      setText("");
      setThread((prev) => ({ ...prev, busy: true, error: null }));
      await apiFetch(ocPath(id, `session/${sid}/prompt_async`), {
        method: "POST",
        body: JSON.stringify({ model: { providerID: "mopan", modelID: model }, parts: [{ type: "text", text: prompt }] }),
      });
      if (id !== wsId) {
        // 새 작업: wsId 효과가 스트림을 열고 이 대화를 연다. 그 전에 세션을 현재 것으로 두고 사용자 말풍선을
        // 먼저 그려 openSession이 화면을 비우지 않게 한다.
        pendingSessionRef.current = sid;
        sessionRef.current = sid;
        setSessionId(sid);
        setThread({
          ...EMPTY_THREAD,
          busy: true,
          messages: [
            {
              info: { id: `local-${Date.now()}`, sessionID: sid, role: "user", time: { created: Date.now() } },
              parts: [{ id: "local-p0", sessionID: sid, messageID: `local-${Date.now()}`, type: "text", text: prompt }],
            } as unknown as OcMessage,
          ],
        });
        setWsId(id);
      } else {
        void loadSessions(id);
      }
    } catch (e) {
      setError(errorMessage(e));
      setThread((prev) => ({ ...prev, busy: false }));
    }
  }

  async function abort() {
    if (!wsId || !sessionId) return;
    try {
      await apiFetch(ocPath(wsId, `session/${sessionId}/abort`), { method: "POST", body: "{}" });
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function deleteSession(s: OcSession) {
    if (!wsId) return;
    try {
      await apiFetch(ocPath(wsId, `session/${s.id}`), { method: "DELETE" });
      if (sessionId === s.id) void openSession(wsId, null);
      void loadSessions(wsId);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function createWorkspace() {
    const name = newName.trim();
    if (!name) return;
    try {
      const w = await apiFetch<Workspace>("/api/code/workspaces", { method: "POST", body: JSON.stringify({ name }) });
      setDialog(null);
      setNewName("");
      await loadWorkspaces();
      setWsId(w.id);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  /** 첨부 = 작업 공간에 올리고 프롬프트에 파일명을 적어 준다. 에이전트는 그 파일을 읽어서 일한다. */
  async function attach(files: FileList | null) {
    if (!files || files.length === 0) return;
    if (!wsId && !simple) return;
    const form = new FormData();
    const names: string[] = [];
    for (const f of Array.from(files)) {
      form.append("files", f);
      const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
      form.append("paths", rel);
      names.push(rel);
    }
    setUploading(true);
    try {
      const id = wsId ?? (await ensureTask(`작업 ${new Date().toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}`));
      await apiFetch<{ written: number }>(`/api/code/workspaces/${encodeURIComponent(id)}/files`, { method: "POST", body: form });
      if (id !== wsId) setWsId(id);
      setError(null);
      setFilesKey((k) => k + 1);
      setText((t) => `${t}${t && !t.endsWith(" ") ? " " : ""}(첨부: ${names.join(", ")}) `);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setUploading(false);
    }
  }

  // 지금 도는 도구 - 입력창 옆 상태 줄. 마지막 assistant 메시지에서 running 상태의 도구를 찾는다.
  const runningTool = (() => {
    for (let i = thread.messages.length - 1; i >= 0; i--) {
      const m = thread.messages[i];
      if (m.info.role !== "assistant") continue;
      const t = [...m.parts].reverse().find((p) => p.type === "tool" && p.state?.status === "running");
      if (t) return `${TOOL_LABEL[t.tool ?? ""] ?? t.tool} · ${toolSummary(t)}`;
      break;
    }
    return null;
  })();

  const serverWs = workspaces?.filter((w) => w.kind === "server") ?? [];
  const localWs = workspaces?.filter((w) => w.kind === "local") ?? [];
  const isEmptyThread = thread.messages.length === 0 && !thread.busy && !sessionId;

  const rail = (
    <aside className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-y-auto bg-surface-container-low p-3">
      {!simple && (
      <section>
        <div className="flex items-center justify-between px-1">
          <h2 className="text-caption tracking-wide text-on-surface-variant">작업 공간</h2>
          <button type="button" className="btn-text btn-compact" onClick={() => setDialog("new")} disabled={!status?.server_available}>
            + 새로
          </button>
        </div>
        {status && !status.server_available && <p className="mt-1 px-1 text-caption text-on-surface-variant">{status.server_reason}</p>}
        <ul className="mt-1 space-y-0.5">
          {serverWs.map((w) => (
            <WorkspaceRow key={w.id} w={w} active={w.id === wsId} onPick={() => (setWsId(w.id), setRailOpen(false))} onDelete={() => setDeleteWs(w)} />
          ))}
          {localWs.map((w) => (
            <WorkspaceRow key={w.id} w={w} active={w.id === wsId} onPick={() => (setWsId(w.id), setRailOpen(false))} />
          ))}
        </ul>
        <button type="button" className="btn-tonal btn-compact mt-2 w-full" onClick={() => setDialog("connect")}>
          {status?.bridge.connected ? `내 컴퓨터 연결됨 · ${status.bridge.host ?? ""}` : "내 컴퓨터 연결"}
        </button>
      </section>
      )}
      {simple && (
        <section className="min-h-0 flex-1">
          <div className="flex items-center justify-between px-1">
            <h2 className="text-caption tracking-wide text-on-surface-variant">지난 작업</h2>
            <button type="button" className="btn-text btn-compact" onClick={() => (setWsId(null), setRailOpen(false))}>
              + 새 작업
            </button>
          </div>
          <ul className="mt-1 space-y-0.5">
            {(workspaces ?? []).map((w) => (
              <WorkspaceRow key={w.id} w={w} active={w.id === wsId} onPick={() => (setWsId(w.id), setRailOpen(false))} onDelete={() => setDeleteWs(w)} />
            ))}
            {workspaces?.length === 0 && <li className="px-2 py-1 text-caption text-on-surface-variant">아직 없습니다. 첫 요청을 보내면 작업이 생깁니다.</li>}
          </ul>
          <p className="mt-3 px-2 text-caption text-on-surface-variant">작업 파일은 마지막 사용 후 30일이 지나면 자동으로 지워집니다. 필요한 결과는 받아 두세요.</p>
        </section>
      )}
      {ws && !simple && (
        <section className="min-h-0 flex-1">
          <div className="flex items-center justify-between px-1">
            <h2 className="text-caption tracking-wide text-on-surface-variant">대화</h2>
            <button type="button" className="btn-text btn-compact" onClick={() => (void openSession(ws.id, null), setRailOpen(false))}>
              + 새 대화
            </button>
          </div>
          <ul className="mt-1 space-y-0.5">
            {sessions.map((s) => (
              <li key={s.id} className="group flex items-center">
                <button
                  type="button"
                  onClick={() => (void openSession(ws.id, s.id), setRailOpen(false))}
                  className={`min-w-0 flex-1 truncate rounded-lg px-2 py-1.5 text-left text-label transition-colors duration-150 hover:bg-surface-container ${s.id === sessionId ? "bg-surface-container-high text-on-surface" : "text-on-surface-variant"}`}
                  title={s.title}
                >
                  {s.title || "새 대화"}
                </button>
                <button type="button" className="icon-btn h-8 w-8 opacity-0 group-hover:opacity-100 focus:opacity-100" aria-label="대화 삭제" onClick={() => void deleteSession(s)}>
                  <TrashIcon />
                </button>
              </li>
            ))}
            {sessions.length === 0 && <li className="px-2 py-1 text-caption text-on-surface-variant">첫 요청을 보내면 대화가 생깁니다.</li>}
          </ul>
        </section>
      )}
    </aside>
  );

  return (
    <div className="flex h-full">
      <div className="hidden h-full md:block">{rail}</div>
      {railOpen && (
        <div role="dialog" aria-modal="true" aria-label={simple ? "지난 작업" : "작업 공간"} className="fixed inset-0 z-30 flex md:hidden">
          <div className="motion-drawer h-full">{rail}</div>
          <button type="button" aria-label="닫기" className="motion-scrim flex-1 bg-scrim" onClick={() => setRailOpen(false)} />
        </div>
      )}

      <section className="flex min-w-0 flex-1 flex-col">
        {/* 머리 한 줄: (모바일) 작업 공간 버튼 · 제목 · 파일. 모델·자동 승인은 채팅과 같이 입력창 안의 칩이다. 왼쪽 48px은 사이드바 햄버거 자리. */}
        <header ref={headerRef} className="relative flex h-10 items-center gap-2 px-4 pl-16 pt-0 md:px-6 md:pl-6" style={{ marginTop: "1rem" }}>
          {/* 코워크는 작업 공간을 고르지 않는다 - 요청이 곧 새 작업이다. 지난 작업만 시계 아이콘으로. */}
          {simple ? (
            <button type="button" aria-label="지난 작업" title="지난 작업" className="icon-btn bg-surface-container text-on-surface shadow-md md:hidden" onClick={() => setRailOpen(true)}>
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="8.5" />
                <path d="M12 7.5V12l3 2" />
              </svg>
            </button>
          ) : (
            <button type="button" aria-label="작업 공간" title="작업 공간" className="icon-btn bg-surface-container text-on-surface shadow-md md:hidden" onClick={() => setRailOpen(true)}>
              <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 8a2 2 0 0 1 2-2h3.2l1.8 2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
              </svg>
            </button>
          )}
          <h1 className="pointer-events-none absolute inset-x-0 truncate px-28 text-center text-title font-medium text-on-surface md:pointer-events-auto md:static md:min-w-0 md:flex-1 md:px-0 md:text-left">
            {ws ? ws.name : copy.title}
            {ws && !simple && <span className="ml-2 text-caption font-normal text-on-surface-variant">{ws.kind === "local" ? "내 컴퓨터" : "서버"}</span>}
          </h1>
          <div className="flex-1 md:hidden" aria-hidden="true" />
          {/* 코워크: 넣은 것·만든 것이 함께 있는 '작업 파일'. 코드: 서버 작업 공간만(내 컴퓨터 폴더는 이미 손에 있다). */}
          {/* 코워크는 작업이 아직 없어도(초안) 항상 보인다 - 누르면 지난 작업을 골라 그 파일을 본다. */}
          {(simple || (ws && ws.kind !== "local")) && (
            <button
              type="button"
              className={`btn-compact ${filesOpen ? "btn-filled" : "btn-tonal"}`}
              onClick={() => setFilesOpen((v) => !v)}
              aria-pressed={filesOpen}
            >
              {simple ? "작업 파일" : "파일"}
            </button>
          )}
        </header>
        <div className="px-4 pt-2 md:px-6">
          <ErrorBanner message={error} />
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-6">
          {!ws && !simple ? (
            <div className="mx-auto max-w-measure rounded-md bg-surface-container-low p-6 text-body text-on-surface">
              <p className="text-title font-medium">{copy.title}</p>
              <p className="mt-2 text-on-surface-variant">
                {workspaces === null
                  ? "준비하는 중..."
                  : status?.server_available
                    ? "작업 공간을 만드는 중..."
                    : `이 서버에서는 서버 작업 공간을 쓸 수 없습니다(${status?.server_reason || "이유 미상"}).${simple ? " 관리자에게 문의해 주세요." : " ‘내 컴퓨터 연결’로 내 PC의 폴더를 쓰세요."}`}
              </p>
              {!simple && (
                <button type="button" className="btn-filled mt-4" onClick={() => setDialog("connect")}>
                  내 컴퓨터 연결
                </button>
              )}
            </div>
          ) : !ws || isEmptyThread ? (
            <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center gap-6 text-center">
              <div>
                <p className="text-headline font-medium text-on-surface">{simple ? "무엇을 해 드릴까요?" : "무엇을 만들까요?"}</p>
                <p className="mt-2 text-body text-on-surface-variant">
                  {simple
                    ? (ws ? "‘" + ws.name + "’ 작업입니다. " : "할 일을 적거나 자료를 넣으면 새 작업이 시작됩니다. ") +
                      "에이전트가 끝까지 진행하고, 넣은 파일과 만든 파일은 ‘작업 파일’에 함께 남습니다(zip 하나면 풀어서 넣습니다). 마지막 사용 후 30일이 지나면 지워지니 결과는 받아 두세요."
                    : "‘" + (ws?.name ?? "") + "’ 폴더 안에서 파일을 읽고 고치고 명령을 실행합니다. 파일 수정과 명령 실행은 매번 확인을 묻고, 도구 호출을 모두 보입니다." +
                      (ws?.kind === "server" ? " 결과물은 ‘파일’에서 받습니다." : " 결과는 내 컴퓨터의 그 폴더에 바로 남습니다.")}
                </p>
              </div>
              <ul className="flex flex-wrap justify-center gap-2">
                {copy.suggestions.map((s) => (
                  <li key={s}>
                    <button type="button" className="rounded-lg bg-surface-container-high px-3 py-2 text-left text-label text-on-surface transition-colors duration-150 hover:bg-surface-container-highest" onClick={() => setText(s)}>
                      {s}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-3xl space-y-4">
              {/* 대화 열은 아래 프롬프트 창과 같은 max-w-3xl·같은 좌우 여백 - 한 줄이 너무 길면 읽기 힘들고, 좁힌 만큼
                  오른쪽 파일 패널이 넓어진다(소유자 지적 2026-09-26: 대화가 프롬프트 창보다 넓어 가독성이 떨어짐). */}
              {thread.messages.length === 0 && sessionId && !thread.busy && <p className="text-body text-on-surface-variant">대화를 불러오는 중...</p>}
              <Thread messages={thread.messages} compact={simple} />
              {thread.permissions.map((p) => (
                <PermissionCard key={p.id} permission={p} onReply={(r) => void reply(p, r)} />
              ))}
              {thread.questions.map((q) => (
                <QuestionCard
                  key={q.id}
                  question={q}
                  onAnswer={(answers) => ws && void apiFetch(ocPath(ws.id, `question/${q.id}/reply`), { method: "POST", body: JSON.stringify({ answers }) }).catch((e) => setError(errorMessage(e)))}
                  onReject={() => ws && void apiFetch(ocPath(ws.id, `question/${q.id}/reject`), { method: "POST", body: "{}" }).catch((e) => setError(errorMessage(e)))}
                />
              ))}
              {thread.error && <ErrorBanner message={thread.error} />}
              {ws && !thread.busy && diff.length > 0 && <ChangedFiles ws={ws} diff={diff} onOpenFiles={() => setFilesOpen(true)} />}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {(ws || simple) && (
          <div className="px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-2 md:px-6">
           <div className="mx-auto w-full max-w-3xl">
            <CodeComposer
              value={text}
              onChange={setText}
              onSubmit={() => void send()}
              sending={thread.busy}
              onStop={() => void abort()}
              models={models.filter((m) => (status?.models ?? []).some((x) => x.id === m.id))}
              model={model}
              onModelChange={setModel}
              autoApprove={autoApprove}
              onAutoApproveChange={setAutoApprove}
              canAttach={simple || ws?.kind === "server"}
              canAttachFolder={simple}
              onFiles={(files) => void attach(files)}
              onOpenFiles={() => setFilesOpen(true)}
              disabled={ws ? !ws.online : false}
              placeholder={!ws || ws.online ? (simple ? "무엇을 해 드릴까요? 자료는 + 로 넣어 주세요." : "만들거나 고칠 것을 적어 주세요.") : "내 컴퓨터가 연결되어 있지 않습니다."}
              statusText={uploading ? "올리는 중..." : thread.busy ? (runningTool ? `실행 중 · ${runningTool}` : "생각하는 중...") : null}
            />
          </div>
          </div>
        )}
      </section>

      {filesOpen && (ws || simple) && (
        <>
          <div className="hidden h-full md:block">
            {ws ? <FilesPanel ws={ws} refreshKey={filesKey} onClose={() => setFilesOpen(false)} /> : <TaskFilesPicker tasks={workspaces ?? []} onPick={setWsId} onClose={() => setFilesOpen(false)} />}
          </div>
          <div className="motion-bottom-sheet fixed inset-0 z-30 flex flex-col bg-surface md:hidden">
            {ws ? <FilesPanel ws={ws} refreshKey={filesKey} onClose={() => setFilesOpen(false)} /> : <TaskFilesPicker tasks={workspaces ?? []} onPick={setWsId} onClose={() => setFilesOpen(false)} />}
          </div>
        </>
      )}

      {dialog === "new" && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-scrim p-4" onClick={() => setDialog(null)}>
          <form
            role="dialog"
            aria-modal="true"
            className="w-full max-w-sm rounded-lg bg-surface p-6 shadow-dialog"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault();
              void createWorkspace();
            }}
          >
            <h2 className="text-title font-medium text-on-surface">새 작업 공간</h2>
            <p className="mt-1 text-caption text-on-surface-variant">서버의 격리된 폴더입니다. 프로젝트마다 하나씩 두면 파일이 섞이지 않습니다.</p>
            <input className="field mt-3 w-full" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="예) 농약안전 대시보드" maxLength={60} />
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="btn-text" onClick={() => setDialog(null)}>
                취소
              </button>
              <button type="submit" className="btn-filled" disabled={!newName.trim()}>
                만들기
              </button>
            </div>
          </form>
        </div>
      )}
      <ConnectDialog open={dialog === "connect"} onClose={() => (setDialog(null), void loadStatus(), void loadWorkspaces())} status={status} />
      {deleteWs && (
        <ConfirmDialog
          title="작업 공간 삭제"
          message={`‘${deleteWs.name}’과 그 안의 파일·대화 기록이 모두 지워집니다. ‘파일 → 전체 zip’으로 먼저 보관할 수 있습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/code/workspaces/${encodeURIComponent(deleteWs.id)}`, { method: "DELETE" });
            if (wsId === deleteWs.id) setWsId(null);
            await loadWorkspaces();
          }}
          onClose={() => setDeleteWs(null)}
        />
      )}
    </div>
  );
}

/** 이번 대화가 바꾼 파일 - 결과물을 손에 쥐는 첫 자리. 서버 작업 공간이면 파일마다 바로 받는다. */
function ChangedFiles({ ws, diff, onOpenFiles }: { ws: Workspace; diff: FileDiff[]; onOpenFiles: () => void }) {
  const add = diff.reduce((n, d) => n + d.additions, 0);
  const del = diff.reduce((n, d) => n + d.deletions, 0);
  return (
    <div className="rounded-md bg-surface-container-low p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-label font-medium text-on-surface">변경 파일 {diff.length}개</span>
        <span className="text-caption text-on-surface-variant">
          +{add} −{del}
        </span>
        <span className="flex-1" />
        {ws.kind !== "local" && (
          <a href={`/api/code/workspaces/${encodeURIComponent(ws.id)}/download`} className="btn-tonal btn-compact">
            전체 zip 받기
          </a>
        )}
        <button type="button" className="btn-text btn-compact" onClick={onOpenFiles}>
          {ws.kind === "cowork" ? "작업 파일 보기" : "파일 보기"}
        </button>
      </div>
      <ul className="mt-2 space-y-1">
        {diff.map((d) => (
          <li key={d.path}>
            <details>
              <summary className="flex cursor-pointer items-center gap-2 text-caption">
                <span className={`w-4 shrink-0 font-mono ${d.status === "deleted" ? "text-error" : "text-primary"}`}>{d.status === "added" ? "A" : d.status === "deleted" ? "D" : "M"}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-on-surface">{d.path}</span>
                <span className="shrink-0 text-on-surface-variant">
                  +{d.additions} −{d.deletions}
                </span>
                {ws.kind !== "local" && d.status !== "deleted" && (
                  <a href={`/api/code/workspaces/${encodeURIComponent(ws.id)}/file?path=${encodeURIComponent(d.path)}`} className="shrink-0 text-primary" onClick={(e) => e.stopPropagation()}>
                    받기
                  </a>
                )}
              </summary>
              <pre className="mt-1 max-h-80 overflow-auto whitespace-pre rounded-sm bg-surface-container-high p-2 text-caption">{d.patch}</pre>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 코워크 초안(아직 작업 없음)에서 '작업 파일'을 누르면: 어느 작업의 파일을 볼지 고르는 같은 틀의 패널. */
function TaskFilesPicker({ tasks, onPick, onClose }: { tasks: Workspace[]; onPick: (id: string) => void; onClose: () => void }) {
  return (
    <aside className="flex h-full w-full flex-col bg-surface-container-low md:w-[26rem] xl:w-[34rem]">
      <div className="flex items-center gap-2 px-3 py-3">
        <h2 className="min-w-0 flex-1 truncate text-label font-medium text-on-surface">작업 파일</h2>
        <button type="button" className="icon-btn h-8 w-8" aria-label="닫기" onClick={onClose}>
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
      <p className="px-3 text-caption text-on-surface-variant">{tasks.length > 0 ? "파일을 볼 작업을 고르세요." : "아직 작업이 없습니다. 첫 요청을 보내면 작업 폴더가 생기고, 넣은 파일과 만든 파일이 여기에 남습니다."}</p>
      <ul className="mt-2 min-h-0 flex-1 overflow-y-auto px-2">
        {tasks.map((t) => (
          <li key={t.id}>
            <button type="button" onClick={() => onPick(t.id)} className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left hover:bg-surface-container">
              <span className="min-w-0 flex-1 truncate text-label text-on-surface">{t.name}</span>
              <span className="shrink-0 text-caption text-on-surface-variant">
                파일 {t.files ?? 0}개{t.updated_at ? ` · ${new Date(t.updated_at * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })}` : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function TrashIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
    </svg>
  );
}

function WorkspaceRow({ w, active, onPick, onDelete }: { w: Workspace; active: boolean; onPick: () => void; onDelete?: () => void }) {
  return (
    <li className="group flex items-center">
      <button
        type="button"
        onClick={onPick}
        className={`flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-label transition-colors duration-150 hover:bg-surface-container ${active ? "bg-surface-container-high text-on-surface" : "text-on-surface-variant"}`}
        title={w.path}
      >
        <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${w.online ? "bg-primary" : "bg-outline-variant"}`} aria-hidden="true" />
        <span className="truncate">{w.name}</span>
        {w.kind === "local" && <span className="shrink-0 text-caption text-on-surface-variant">PC</span>}
      </button>
      {onDelete && (
        <button type="button" className="icon-btn h-8 w-8 opacity-0 group-hover:opacity-100 focus:opacity-100" aria-label={`${w.name} 삭제`} onClick={onDelete}>
          <TrashIcon />
        </button>
      )}
    </li>
  );
}
