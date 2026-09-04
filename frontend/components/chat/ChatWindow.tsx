"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, approveChat, errorMessage, streamChat } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import Composer, {
  ATTACHMENT_EXTENSIONS,
  type PendingAttachment,
} from "@/components/chat/Composer";
import MessageBubble from "@/components/chat/MessageBubble";
import PlanProgress from "@/components/chat/PlanProgress";
import type {
  Branding,
  User,
  CallableTool,
  WorkflowOption,
  AnswerModel,
  ApprovalRequest,
  Attachment,
  ChatEvent,
  McpToolOption,
  Message,
  PendingToolCall,
  PlanStep,
} from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  planning: "실행 계획 세우는 중…",
  calling_tool: "도구 호출 중…",
  searching: "문서 검색 중…",
  answering: "답변 생성 중…",
};

// settings.max_attachments_per_message and settings.max_attachment_size_mb. The
// server is the real boundary and refuses both in Korean; these only spare the
// user an upload that ends in a 400 or a 413, and they are worded identically to
// the server's own refusals so the two can never read as different rules.
const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_MB = 10;

// The chosen answer model, remembered across messages and across reloads. A
// per-viewer convenience with no server-side meaning - the server re-checks the
// value against its allowlist on every request - so localStorage is the right
// home for it, and a browser that refuses to store it just starts on the
// default every time.
const MODEL_STORAGE_KEY = "mopan.answer-model";

// Whether the Super Agent plans the next question. Remembered the same way and
// for the same reason as the model, and OFF by default: the direct RAG path is
// the default until the orchestrator measures better on the eval set, and a
// browser that refuses to store this starts on the default every time - which is
// the safe direction.
const ORCHESTRATOR_STORAGE_KEY = "mopan.orchestrator";

// Which workflow answers, remembered the same way and validated against the
// list the same way the model is: an admin can disable or delete one, and a
// stale id would then be a 404 or a 409 on every send that the user cannot act
// on. The absence of a stored value is the DEFAULT AGENT, which is this app
// exactly as it behaved before agents existed - the safe direction.
const WORKFLOW_STORAGE_KEY = "mopan.workflow";

// 자동 사용을 끈 MCP 서버 이름들. 기본이 "연결하면 켬"이라 켠 쪽이 아니라 끈
// 쪽을 기억한다 - 새 서버는 등록만 하면 아무도 손대지 않아도 바로 자동 사용
// 후보가 된다(클로드 데스크톱의 방향). 서버 쪽 위험 필터(read 등급만)가 진짜
// 경계라 여기 남은 낡은 이름은 해가 없다.
const MCP_OFF_STORAGE_KEY = "mopan.mcp-servers-off";

/** 시간대별 인사. 경계는 체감으로 골랐고 상수가 곧 문서다 - "안녕하세요" 한
 * 문장이 새벽 2시에도 아침 9시에도 똑같이 나가는 것이 어색하다는 소유자
 * 지적에서 왔다. */
export function timeGreeting(hour: number): string {
  if (hour >= 5 && hour < 11) return "좋은 아침이에요.";
  if (hour >= 11 && hour < 17) return "안녕하세요.";
  if (hour >= 17 && hour < 22) return "좋은 저녁이에요.";
  return "늦은 시간까지 애쓰시네요.";
}

function rejection(file: File): string | null {
  // Same rule as validation.py's extension_of: no dot means no extension, not
  // "the whole name is the extension".
  const extension = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!ATTACHMENT_EXTENSIONS.includes(extension)) {
    return `지원하지 않는 파일 형식입니다: .${extension}`;
  }
  if (file.size > MAX_ATTACHMENT_MB * 1024 * 1024) {
    return `파일이 최대 크기 ${MAX_ATTACHMENT_MB}MB를 초과했습니다.`;
  }
  return null;
}

export default function ChatWindow({
  initialConversationId,
}: {
  initialConversationId: string | null;
}) {
  const router = useRouter();
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId);
  const [messages, setMessages] = useState<Message[]>([]);
  // "no messages", "not loaded yet" and "the load failed" are three states, and
  // the empty-state line below belongs to only the first - the same distinction
  // the Sidebar draws for its conversation list, its `!error &&` included.
  // Without `loaded`, every arrival at /chat/{id} flashes the greeting before
  // the transcript lands: measured at 40ms over loopback, and it is a network
  // round trip, so it is only ever longer in front of a real user. Without
  // `!error`, a failed load shows that same invitation stacked on top of the
  // error banner, because setLoaded runs in finally() and a rejected fetch is
  // therefore loaded-and-empty.
  const [loaded, setLoaded] = useState(!initialConversationId);
  const [input, setInput] = useState("");
  // 브랜딩과 내 프로필 - 첫 화면(마스트헤드)이 그리는 값. 장식이라 실패해도
  // 기본 문구로 그린다.
  const [branding, setBranding] = useState<Branding | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [models, setModels] = useState<AnswerModel[]>([]);
  // Every enabled tool on every enabled server. Empty for a deployment with no
  // MCP server registered, and the picker then renders nothing at all.
  const [tools, setTools] = useState<McpToolOption[]>([]);
  // GET /api/tools - ONE list of everything callable, which is what `@` opens.
  // It overlaps the two lists above rather than replacing them: this one is the
  // MENU, in one namespace, and those two carry the ids POST /api/chat takes.
  const [callables, setCallables] = useState<CallableTool[]>([]);
  // The collection this question is scoped to, chosen from the `@` menu. Null is
  // the whole corpus, which is what every question sent before this existed
  // asked for.
  const [collectionId, setCollectionId] = useState<string | null>(null);
  // ONE pending call. Slice 2 is manual invocation - the user picks the tool -
  // so there is nothing here to plan with; the request body already takes a
  // list because Slice 3's orchestrator will send several.
  const [toolCall, setToolCall] = useState<PendingToolCall | null>(null);
  // "" until GET /api/models has answered. A send in that window carries no
  // `model` at all, which the server reads as its own default - so the picker
  // failing to load costs the user the choice, never the answer.
  const [model, setModel] = useState("");
  // Slice 3, opt-in per question. False on the server too, so a client that
  // never sends the flag gets the Slice 1 path unchanged.
  const [orchestrator, setOrchestrator] = useState(false);
  // 자동 사용을 끈 MCP 서버 이름들. 여기 없는 서버는 전부 켜져 있는 것이다.
  const [mcpOff, setMcpOff] = useState<string[]>([]);
  // Every ENABLED workflow that has a graph. Empty for a deployment with none
  // configured, and the picker then renders nothing at all.
  const [workflows, setWorkflows] = useState<WorkflowOption[]>([]);
  // null is NO workflow, and a send then carries no `workflow_id` - exactly the
  // body this app sent before workflows existed.
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  // The plan, as it runs. Keyed by step id and updated in place, because each
  // step arrives twice - `running`, then its final state.
  const [steps, setSteps] = useState<PlanStep[]>([]);
  // Set by the terminal `approval_required` frame. While it is non-null the plan
  // is paused, the tool has NOT been called, and nothing has been answered.
  const [approval, setApproval] = useState<(ApprovalRequest & { pendingId: string }) | null>(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The answer, repeated into an off-screen live region - see the markup below
  // for why the transcript itself cannot be the live region.
  const [announcement, setAnnouncement] = useState("");
  // A second region, for the things that are not the answer: an attachment
  // added or removed, and 복사됨. Separate because the answer's region is only
  // ever written on `done`, and mixing the two would re-announce an old answer
  // every time a file was attached.
  const [notice, setNotice] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Set only by 중지, so the shared AbortError catch below can tell "the user
  // pressed stop" from "this component unmounted mid-answer".
  const stoppedRef = useRef(false);
  // One controller per in-flight upload, so removing a chip that is still
  // uploading cancels its request instead of letting it land on a chip that no
  // longer exists.
  const uploadsRef = useRef(new Map<string, AbortController>());
  // Every blob: URL handed to a thumbnail, revoked on unmount. Without this a
  // session of attaching and removing images leaks one buffer per preview.
  const previewUrlsRef = useRef<string[]>([]);
  // dragenter/dragleave fire for every child element the pointer crosses, so a
  // plain boolean flickers off the moment the cursor moves over a message. The
  // depth counter is what makes the drop state survive the crossing.
  const dragDepth = useRef(0);

  // Abort an answer still in flight when this window stops being the one on
  // screen. Without it streamChat outlived the component and its closure kept
  // the old `router`: ask at /chat, click another conversation mid-answer, and
  // ~3.5s later the abandoned stream's `done` frame ran router.replace and
  // threw the browser onto a conversation the user never chose.
  //
  // Keyed on initialConversationId, not []: /chat/{a} -> /chat/{b} is the same
  // component in the same slot, so React re-renders it with a new prop rather
  // than unmounting it, and a []-keyed cleanup never runs for the case that
  // actually reproduced.
  useEffect(() => () => abortRef.current?.abort(), [initialConversationId]);

  // 입력창은 항상 받아쓸 준비가 되어 있어야 한다. 첫 질문의 전송은 /chat ->
  // /chat/{id} 라우트 전환이라 이 컴포넌트가 통째로 갈리고, 그때 포커스가
  // 죽는다 - 다음 질문을 치려면 창을 다시 클릭해야 했다(소유자 지적). 마운트와
  // 대화 전환마다 되돌려 두면 전송 직후에도, 옛 대화를 열어도 바로 이어 칠 수
  // 있다.
  useEffect(() => {
    textareaRef.current?.focus();
  }, [initialConversationId]);

  useEffect(() => {
    const urls = previewUrlsRef.current;
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  // Once per mount, and deliberately not wired to `error`: the model list is a
  // convenience, and a failure to fetch it must not put a red banner over a
  // conversation that answers perfectly well on the server's default.
  //
  // localStorage is read HERE rather than in a useState initialiser: this
  // component is server-rendered, where `window` does not exist, and reading it
  // during render would also hydrate a different value than the server emitted.
  useEffect(() => {
    apiFetch<AnswerModel[]>("/api/models")
      .then((list) => {
        setModels(list);
        let stored: string | null = null;
        try {
          stored = localStorage.getItem(MODEL_STORAGE_KEY);
        } catch {
          // Private mode, or site data blocked. Fall through to the default.
        }
        // Validated against the list, not trusted: an admin can remove a model
        // from ANSWER_MODELS, and a stale id would then be refused on every
        // send with a 400 the user cannot act on.
        const fallback = list.find((m) => m.is_default)?.id ?? list[0]?.id ?? "";
        setModel(list.some((m) => m.id === stored) ? stored! : fallback);
      })
      .catch(() => setModels([]));
    // Same rule as the model list: a deployment with no MCP server answers with
    // [], and a failure here leaves the 도구 button hidden rather than putting a
    // banner over a conversation that answers fine without it.
    apiFetch<McpToolOption[]>("/api/mcp/tools")
      .then(setTools)
      .catch(() => setTools([]));
    // Same rule: a failure here costs the user the `@` menu, never the question.
    apiFetch<CallableTool[]>("/api/tools")
      .then(setCallables)
      .catch(() => setCallables([]));
    apiFetch<Branding>("/api/branding").then(setBranding).catch(() => {});
    apiFetch<User>("/api/auth/me").then(setMe).catch(() => {});
    // Same rule again, and it matters most here: a deployment with no
    // workflows answers with [], the picker disappears, and every send carries
    // no `workflow_id` - which is the app as it was. A failure to load them must
    // never stop a question being asked.
    apiFetch<WorkflowOption[]>("/api/workflows/selectable")
      .then((list) => {
        setWorkflows(list);
        let stored: string | null = null;
        try {
          stored = localStorage.getItem(WORKFLOW_STORAGE_KEY);
        } catch {
          // Private mode, or site data blocked. Fall through to the default.
        }
        // Validated against the list, never trusted: a workflow an admin
        // disabled is absent from it, and a stale id would be a 409 on every
        // send.
        setWorkflowId(list.some((w) => w.id === stored) ? stored : null);
      })
      .catch(() => setWorkflows([]));
    // Read HERE and not in a useState initialiser, for the same two reasons the
    // model is: this component is server-rendered, where `window` does not
    // exist, and reading during render would hydrate a different value than the
    // server emitted.
    try {
      setOrchestrator(localStorage.getItem(ORCHESTRATOR_STORAGE_KEY) === "true");
    } catch {
      // Private mode, or site data blocked. The default is off, which is where
      // this already is.
    }
    try {
      const stored = JSON.parse(localStorage.getItem(MCP_OFF_STORAGE_KEY) ?? "[]");
      if (Array.isArray(stored)) setMcpOff(stored.filter((n) => typeof n === "string"));
    } catch {
      // Same fallback, and here "the default" means every server ON.
    }
  }, []);

  useEffect(() => {
    if (!initialConversationId) return;
    apiFetch<Message[]>(`/api/conversations/${initialConversationId}/messages`)
      .then(setMessages)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoaded(true));
  }, [initialConversationId]);

  useEffect(() => {
    // §7: under `reduce` the app must be fully usable with zero animation, and
    // a CSS override cannot reach a behavior passed to scrollIntoView. The jump
    // still lands on the same element - only the tween goes.
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    bottomRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [messages, status]);

  async function upload(key: string, file: File) {
    const controller = new AbortController();
    uploadsRef.current.set(key, controller);
    const form = new FormData();
    form.append("file", file);
    try {
      // apiFetch handles FormData correctly: it only sets a JSON Content-Type
      // for string bodies, so the browser's multipart boundary survives.
      const created = await apiFetch<Attachment>("/api/attachments", {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      setAttachments((prev) =>
        prev.map((a) =>
          a.key === key
            ? { ...a, status: "ready", attachment: created, sizeBytes: created.size_bytes }
            : a,
        ),
      );
      setNotice(`${created.filename} 첨부됨`);
    } catch (err) {
      // The chip was removed while this was in flight; there is nothing left to
      // report the failure on.
      if ((err as { name?: string } | null)?.name === "AbortError") return;
      const message = errorMessage(err);
      // Onto the chip, not into the page banner: with five files in the row a
      // banner cannot say which one 지원하지 않는 파일 형식입니다 is about.
      setAttachments((prev) =>
        prev.map((a) => (a.key === key ? { ...a, status: "error", error: message } : a)),
      );
      setNotice(`${file.name} 첨부 실패: ${message}`);
    } finally {
      uploadsRef.current.delete(key);
    }
  }

  function addFiles(files: File[]) {
    setError(null);
    const room = MAX_ATTACHMENTS - attachments.length;
    if (files.length > room) {
      // The one refusal that has no chip to live on, because the files it
      // refuses never become chips. Same sentence the server answers with.
      setError(`첨부파일은 한 번에 최대 ${MAX_ATTACHMENTS}개까지 보낼 수 있습니다.`);
    }
    for (const file of files.slice(0, Math.max(room, 0))) {
      const key = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const refusal = rejection(file);
      const isImage = file.type.startsWith("image/");
      // A blob: URL, not a FileReader data: URL: it is synchronous, so the
      // thumbnail is on screen in the same frame the file was chosen.
      const previewUrl = isImage && !refusal ? URL.createObjectURL(file) : null;
      if (previewUrl) previewUrlsRef.current.push(previewUrl);
      setAttachments((prev) => [
        ...prev,
        {
          key,
          filename: file.name,
          sizeBytes: file.size,
          kind: isImage ? "image" : "document",
          previewUrl,
          status: refusal ? "error" : "uploading",
          attachment: null,
          error: refusal,
        },
      ]);
      // The upload starts NOW, on selection, not on send: the thumbnail and any
      // refusal have to be on screen while the user is still writing the
      // question, not after they have pressed 전송.
      if (!refusal) void upload(key, file);
      else setNotice(`${file.name} 첨부 실패: ${refusal}`);
    }
  }

  function removeAttachment(key: string) {
    const entry = attachments.find((a) => a.key === key);
    if (!entry) return;
    uploadsRef.current.get(key)?.abort();
    if (entry.previewUrl) URL.revokeObjectURL(entry.previewUrl);
    setAttachments((prev) => prev.filter((a) => a.key !== key));
    setNotice(`${entry.filename} 첨부 삭제됨`);
    // Only a row that actually exists server-side. A refused file was never
    // stored, and DELETE on its (absent) id would answer 404 and put a banner
    // on screen for a removal that worked.
    if (entry.attachment) {
      apiFetch(`/api/attachments/${entry.attachment.id}`, { method: "DELETE" }).catch((err) =>
        setError(errorMessage(err)),
      );
    }
  }

  /** One streamed turn, from either endpoint.
   *
   * `start` is `streamChat` for a new question and `approveChat` for the second
   * half of a plan that paused. Everything after the request is identical - the
   * same frames, the same `done` handling, the same abort and truncation rules -
   * and two copies of it would have diverged on the first fix. */
  async function run(
    question: string,
    pendingId: string,
    start: (onEvent: (event: ChatEvent) => void, signal: AbortSignal) => Promise<void>,
  ) {
    const controller = new AbortController();
    abortRef.current = controller;
    stoppedRef.current = false;
    setError(null);
    setAnnouncement("");
    // The pause is over the moment a new stream starts, whichever way it ends.
    setApproval(null);
    setSending(true);

    try {
      let newConversationId: string | null = null;
      // Neither `token` nor `citations` gets a branch, both deliberately:
      // answer() is a single non-streaming llm_provider.chat() call so `token`
      // is never emitted at all, and the `citations` frame carries the identical
      // array that `done` carries one frame later.
      await start((event) => {
          if (event.type === "status") {
            setStatus(STATUS_LABEL[event.status] ?? null);
          } else if (event.type === "step") {
            // Upsert: every step arrives twice, `running` then its final state.
            setSteps((prev) => {
              const next = prev.filter((s) => s.id !== event.id);
              return [...next, event];
            });
          } else if (event.type === "approval_required") {
            // TERMINAL. The plan stopped, the tool has not run and no answer is
            // coming until this is answered - so the question bubble stays on
            // screen and the card below the transcript takes over.
            setApproval({ ...event, pendingId });
            setNotice(`${event.step.tool} 도구 실행 승인이 필요합니다.`);
          } else if (event.type === "error") {
            setError(event.detail);
            // Take the question back off screen with it. An `error` frame means
            // the backend rolled the conversation back - a brand new one is
            // deleted, an existing one keeps neither message - so leaving the
            // bubble up shows a question that is not saved anywhere, and the
            // next reload silently loses it. Only this branch does it: a
            // truncated stream or a dropped connection throws instead, and
            // there the backend may well have committed the exchange.
            setMessages((prev) => prev.filter((m) => m.id !== pendingId));
          } else if (event.type === "done") {
            newConversationId = event.conversation_id;
            setAnnouncement(event.content);
            // The plan goes when the answer arrives, exactly as the status line
            // does. It is rendered after the transcript, so leaving it up put
            // "문서 검색: …" UNDER the answer it produced - seen in a screenshot,
            // not in the markup - and it would then sit there through the next
            // question. The permanent record is the 추적 dialog, which shows the
            // plan with each step's timing and result.
            setSteps([]);
            setMessages((prev) => [
              ...prev,
              {
                // The row id from the `done` frame, not a fabricated
                // `assistant-${Date.now()}`: the 👍/👎 and 추적 controls call
                // /api/messages/{id}/..., so a made-up id made both of them
                // 404 on the answer that had just arrived.
                id: event.message_id,
                role: "assistant",
                content: event.content,
                prompt_name: event.prompt_name ?? null,
                citations: event.citations,
                attachments: [],
                feedback: null,
                // From the frame, not from `model` state: the user may well
                // switch the picker while this answer is still streaming, and
                // the label has to name what actually answered.
                model: event.model,
                // From the frame for the same reason the model is: the picker
                // may have moved while this answer was streaming, and the label
                // has to name what actually produced it.
                workflow_name: event.workflow_name,
                workflow_version: event.workflow_version ?? null,
                created_at: new Date().toISOString(),
              },
            ]);
          }
      }, controller.signal);

      if (!conversationId && newConversationId) {
        setConversationId(newConversationId);
        // router.replace, and NOT window.history.replaceState. Both were
        // measured on `next start`, same clicks, only this line differing, with
        // POST /api/chat answered by a stubbed SSE body naming an existing
        // conversation.
        //
        // router.replace costs a full document load here (performance.timeOrigin
        // changes; /api/auth/me and /api/conversations are requested again), so
        // the answer that just rendered is off screen until the new page's
        // transcript fetch lands: rAF frames of the new document at 31, 53 and
        // 63ms hold no messages and the transcript is back at 80ms, ~76ms end to
        // end over loopback. Everything downstream is then correct - Back
        // re-requests /api/conversations/{id}/messages and restores the
        // conversation, Forward returns to the one clicked in the sidebar, and
        // reload matches both.
        //
        // window.history.replaceState removes that reload, and the Sidebar still
        // refetches because usePathname() still changes. It also corrupts the
        // history entry, which is worse. Next patches replaceState to re-run its
        // router restore with the tree it already has, so the entry keeps the
        // /chat (new-chat) tree while its URL becomes /chat/{id}. Measured: the
        // next sidebar click degrades to a full page load, and Back then restores
        // that entry making NO request at all - the transcript it showed was the
        // two messages left in memory where the conversation has four, and
        // nothing ever refetches it. 76ms of flicker is cosmetic; a history entry
        // whose page disagrees with its URL is not.
        router.replace(`/chat/${newConversationId}`);
      }
    } catch (err) {
      // An abort is this component's own doing, not a failure: either the user
      // pressed 중지, or they moved on and the unmount cleanup fired. Rendering
      // it would put a red banner on the conversation they just opened, about
      // the one they just left. Name check rather than `instanceof
      // DOMException` - fetch and the stream reader are free to reject with
      // either, and only the name is guaranteed.
      if ((err as { name?: string } | null)?.name !== "AbortError") {
        setError(errorMessage(err));
      } else if (stoppedRef.current) {
        // 중지 lands before phase 3, so the backend persisted nothing: the
        // client disconnect cancels the generator at a yield, and persist_turn
        // is downstream of that. Leaving the question in the transcript would
        // show a turn that no reload can reproduce - the same reasoning the
        // `error` frame above follows - so it goes back into the composer, where
        // the user can edit it and ask again.
        setMessages((prev) => prev.filter((m) => m.id !== pendingId));
        setInput(question);
        setNotice("답변 생성을 중지했습니다.");
      }
    } finally {
      setStatus(null);
      setSending(false);
    }
  }

  async function handleSend() {
    if (!input.trim() || sending) return;
    if (attachments.some((a) => a.status === "uploading")) {
      setError("첨부파일 업로드가 끝난 뒤에 보내 주세요.");
      return;
    }

    const question = input;
    const sent = attachments.filter((a) => a.attachment !== null).map((a) => a.attachment!);
    const calls = toolCall ? [toolCall] : [];
    const autoToolIds = tools
      .filter((t) => !mcpOff.includes(t.server_name))
      .map((t) => t.id);
    const pendingId = `temp-${Date.now()}`;
    setInput("");
    // 마우스로 전송을 누르면 포커스가 버튼에 남는다. 다음 질문은 언제나 바로
    // 이어서 온다는 것이 이 화면의 전제이므로, 여기서 입력창으로 되돌린다.
    textareaRef.current?.focus();
    // Cleared here rather than on `done`: these rows are claimed by the send,
    // so leaving the chips up would offer a 삭제 that now answers 409
    // 이미 전송된 첨부파일은 삭제할 수 없습니다.
    setAttachments([]);
    // Cleared with the attachments and for the same reason: the call belongs to
    // the turn that was just sent, and leaving the chip up would silently run
    // the tool again on the next question.
    setToolCall(null);
    // The previous turn's plan, not this one's. Cleared on SEND rather than in
    // run(), so the steps of a paused plan survive the approval round trip and
    // the user can still read what has already happened while deciding.
    setSteps([]);
    setMessages((prev) => [
      ...prev,
      {
        id: pendingId,
        role: "user",
        content: question,
        citations: [],
        attachments: sent,
        model: null,
        workflow_name: null,
        feedback: null,
        created_at: new Date().toISOString(),
      },
    ]);

    await run(question, pendingId, (onEvent, signal) =>
      streamChat(
        {
          conversation_id: conversationId,
          message: question,
          attachment_ids: sent.map((a) => a.id),
          // Omitted when the question is not scoped, so the body stays the one
          // this app has always sent. A collection a workflow cannot reach is a
          // Korean 400 from the server before the conversation is created.
          ...(collectionId ? { collection_ids: [collectionId] } : {}),
          ...(calls.length
            ? { tool_calls: calls.map((c) => ({ tool_id: c.tool.id, arguments: c.arguments })) }
            : {}),
          // 켜져 있는 서버의 모든 도구 id. 서버가 read 등급만 걸러 쓰므로 여기서
          // 위험도를 가리지 않고, 비어 있으면 아예 싣지 않아 예전 요청 그대로다.
          ...(autoToolIds.length ? { auto_tool_ids: autoToolIds } : {}),
          // Omitted, not sent empty, while the list is still loading: the
          // backend reads an absent `model` as ANSWER_MODEL and an unknown one
          // as a 400.
          ...(model ? { model } : {}),
          // Omitted when off, so a turn that does not want a plan sends exactly
          // the body Slice 1 sent.
          ...(orchestrator ? { orchestrator: true } : {}),
          // Omitted when no workflow is chosen, for the same reason: the
          // request is then byte-identical to the one this app sent before
          // workflows existed.
          ...(workflowId ? { workflow_id: workflowId } : {}),
        },
        onEvent,
        signal,
      ),
    );
  }

  /** 승인 / 거부 on a paused plan. The second request, carrying the token.
   *
   * `approved: false` is not "cancel" - the plan continues without that step and
   * still answers from whatever else it finds, which is the same rule a failed
   * step follows. The token is single-use server-side, so a double click is a
   * Korean 404 rather than a second call to the tool. */
  async function decide(approved: boolean) {
    if (!approval || sending) return;
    const { approval_token, pendingId, step } = approval;
    setNotice(approved ? `${step.tool} 실행을 승인했습니다.` : `${step.tool} 실행을 거부했습니다.`);
    await run(
      messages.find((m) => m.id === pendingId)?.content ?? "",
      pendingId,
      (onEvent, signal) => approveChat({ approval_token, approved }, onEvent, signal),
    );
  }

  /** MCP 서버 하나의 자동 사용 스위치. `@`로 직접 부르는 길은 이 값과 무관하게
   * 항상 열려 있다 - 이것은 "상황이 맞으면 모델이 알아서 쓴다"의 기본 설정이다. */
  function chooseMcpServer(name: string, on: boolean) {
    setMcpOff((prev) => {
      const next = on ? prev.filter((n) => n !== name) : [...prev.filter((n) => n !== name), name];
      try {
        localStorage.setItem(MCP_OFF_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // The choice still applies to this session; it just will not survive a
        // reload.
      }
      return next;
    });
    setNotice(on ? `${name} 서버를 자동으로 사용합니다.` : `${name} 서버 자동 사용을 껐습니다.`);
  }

  function chooseOrchestrator(value: boolean) {
    setOrchestrator(value);
    setNotice(value ? "슈퍼 에이전트를 켰습니다." : "슈퍼 에이전트를 껐습니다.");
    try {
      localStorage.setItem(ORCHESTRATOR_STORAGE_KEY, String(value));
    } catch {
      // Same as the model: the choice applies to this session and just will not
      // survive a reload.
    }
  }

  /** Picking a workflow also moves the MODEL picker to the workflow's model.
   *
   * The server treats the workflow's model as a default an explicit `model`
   * still overrides, so leaving the picker where it was would send the old model
   * and silently ignore the workflow's - the user would have configured a model
   * on it and never seen it used. Moving the visible control is what makes the
   * two agree, and the user can still change it afterwards. */
  function chooseWorkflow(id: string | null) {
    setWorkflowId(id);
    const workflow = workflows.find((w) => w.id === id) ?? null;
    setNotice(
      workflow ? `${workflow.name} 워크플로우로 답변합니다.` : "워크플로우 없이 답변합니다.",
    );
    if (workflow?.answer_model && models.some((m) => m.id === workflow.answer_model)) {
      setModel(workflow.answer_model);
    }
    try {
      if (id) localStorage.setItem(WORKFLOW_STORAGE_KEY, id);
      else localStorage.removeItem(WORKFLOW_STORAGE_KEY);
    } catch {
      // The choice still applies to this session; it just will not survive a
      // reload. Nothing to tell the user about.
    }
  }

  /** The 문서 검색 rows of the `@` menu. Announced like every other choice made
   * in the composer, because the chip is small and the consequence is not:
   * scoping to one collection is the difference between "the corpus does not
   * say" and "this part of it does not". */
  function chooseCollection(id: string | null) {
    setCollectionId(id);
    const name = callables
      .find((c) => c.kind === "rag")
      ?.collections.find((c) => c.id === id)?.name;
    setNotice(name ? `${name} 분류에서만 찾습니다.` : "분류 제한을 풀었습니다.");
  }

  function chooseModel(id: string) {
    setModel(id);
    setNotice(`답변 모델을 ${models.find((m) => m.id === id)?.label ?? id}(으)로 바꿨습니다.`);
    try {
      localStorage.setItem(MODEL_STORAGE_KEY, id);
    } catch {
      // The choice still applies to this session; it just will not survive a
      // reload. Nothing to tell the user about.
    }
  }

  const hasFiles = (e: React.DragEvent) => e.dataTransfer.types.includes("Files");

  // h-full, not h-screen: this fills `main`, and the (app) layout's h-screen
  // wrapper is what bounds it. h-screen here would be 100vh whatever main
  // actually offers a child. Below md that is not the same number: main is a
  // flex item stretched to the wrapper's 100vh with pt-12 on it, so its content
  // box is 100vh - 3rem and a h-screen child overflows by exactly that padding.
  return (
    <div
      className="relative flex h-full flex-col"
      onDragEnter={(e) => {
        if (!hasFiles(e)) return;
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(e) => {
        // Without preventDefault on dragover the browser refuses the drop and
        // navigates to the file instead, which loses the whole conversation.
        if (hasFiles(e)) e.preventDefault();
      }}
      onDragLeave={() => {
        dragDepth.current -= 1;
        if (dragDepth.current <= 0) setDragging(false);
      }}
      onDrop={(e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        addFiles(Array.from(e.dataTransfer.files));
      }}
    >
      {dragging && (
        // pointer-events-none is load-bearing: an overlay that takes the
        // pointer fires dragleave on the container the instant it appears, so
        // the drop state would flicker and the drop itself would land on the
        // overlay instead of on the handler above.
        <div className="pointer-events-none absolute inset-4 z-10 flex items-center justify-center rounded-lg bg-surface outline-dashed outline-2 outline-primary">
          <p className="text-title text-primary">파일을 놓아 첨부하세요</p>
        </div>
      )}
      {/* The scroll container is full-bleed; the 768px reading column (§6) is
          the inner div. Putting max-width on the scroller instead would leave
          the scrollbar floating in the middle of the page. */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-transcript space-y-8 px-4 py-8 sm:px-6">
          {loaded && !error && messages.length === 0 && !sending && (
            // The masthead. It replaced a one-line invitation - "등록된 문서에
            // 대해 무엇이든 물어보세요." - and four suggestion chips, both
            // removed on the owner's instruction: the chips guessed at
            // questions nobody had, and the line described a document-QA
            // chatbot, which is not what this is.
            //
            // What it says instead is the product's own name, twice. 모판 is
            // the seedling tray a rice farmer raises one crop in and
            // transplants into any number of different fields, and it is what
            // the mascot is carrying; MOPAN is Modular Orchestration Platform
            // for Agent Nexus. The two readings say the same thing from
            // opposite ends, which is why both are on screen and neither needs
            // a sentence explaining it.
            //
            // §2 puts the gradient on the wordmark. It is on 모판 alone here -
            // NOT on the body text under it, which is where it used to be: a
            // whole sentence in a three-stop gradient is a surface treatment
            // wearing a wordmark's clothes.
            <div className="mt-12 flex flex-col items-center text-center">
              {/* 장식용 일반 <img> - 상단 주석 참조. src는 브랜딩을 따른다:
                  업로드된 마스코트가 있으면 그것, 없으면 기본 마스코트. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={branding?.has_custom_mascot ? "/api/branding/mascot" : "/mascot.png"}
                alt=""
                aria-hidden="true"
                width={720}
                height={631}
                className="mb-6 h-auto w-40 md:w-56"
              />
              {/* 제목·문구는 브랜딩이 먼저다 (GET /api/branding, null = 기본).
                  MOPAN은 가져다 자기 것으로 만드는 판이고, 첫 화면의 이름이 그
                  "자기 것"의 첫인상이다. 기본값일 때만 모판(MOPAN) 워드마크와
                  약어 풀이를 그린다 - 남의 제목 밑에 우리 약어를 깔면 그건
                  브랜딩이 아니라 워터마크다. */}
              {branding?.app_title ? (
                <h1 className="max-w-[32rem] break-keep text-display font-medium">
                  <span className="text-gradient-brand">{branding.app_title}</span>
                </h1>
              ) : (
                <>
                  <h1 className="text-display font-medium">
                    <span className="text-gradient-brand">모판</span>
                    <span className="text-on-surface">(MOPAN)</span>
                  </h1>
                  <p className="mt-1 text-caption tracking-wide text-on-surface-variant">
                    <b className="font-medium text-on-surface">M</b>odular{" "}
                    <b className="font-medium text-on-surface">O</b>rchestration{" "}
                    <b className="font-medium text-on-surface">P</b>latform for{" "}
                    <b className="font-medium text-on-surface">A</b>gent{" "}
                    <b className="font-medium text-on-surface">N</b>exus
                  </p>
                </>
              )}
              <hr className="my-5 w-12 border-t border-outline-variant" />
              {/* 호칭 + 시간대 인사. 닉네임은 본인이 계정 창에서 정한 값이라
                  부를 자격이 있다 - 이메일 앞부분으로 어림하는 것은 호칭이
                  아니라 추측이다. 시각은 브라우저의 로컬 시계다: 사용자에게
                  "지금"은 자기 시계이지 서버의 시간대가 아니다. me가 클라이언트
                  fetch 후에만 채워지므로 이 블록은 SSR에 없고, 시(hour)를
                  렌더에서 바로 읽어도 하이드레이션이 어긋나지 않는다. */}
              {me?.nickname && (
                <p className="mb-1 max-w-[32rem] break-keep text-body-lg text-on-surface">
                  {me.nickname}님, {timeGreeting(new Date().getHours())}
                </p>
              )}
              <p className="max-w-[32rem] break-keep text-body-lg text-on-surface">
                {branding?.tagline_primary || "한 판에서 길러 어느 논에나 옮겨 심습니다."}
              </p>
              <p className="mt-2 max-w-[32rem] break-keep text-body text-on-surface-variant">
                {branding?.tagline_secondary ||
                  "RAG · MCP · LLM · 워크플로우를 직접 등록하고 조합하는 베이스 시스템입니다."}
              </p>
              {/* 추천 질문. 옛 판의 칩은 "누구의 것도 아닌 질문을 어림해서"
                  지워졌다 - 이 칩들은 다르다: 이 배포의 관리자가 자기 코퍼스를
                  알고 직접 적은 문장이다. 누르면 입력창에 채워질 뿐 보내지
                  않는다 - 질문의 주인은 끝까지 사용자다. */}
              {(branding?.suggested_questions?.length ?? 0) > 0 && (
                <div className="mt-6 flex max-w-[36rem] flex-wrap justify-center gap-2">
                  {branding?.suggested_questions.map((question) => (
                    <button
                      key={question}
                      type="button"
                      onClick={() => {
                        setInput(question);
                        textareaRef.current?.focus();
                      }}
                      className="max-w-full truncate rounded-full bg-surface-container px-4 py-2 text-label text-on-surface transition-colors duration-150 hover:bg-surface-container-high"
                    >
                      {question}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} onNotify={setNotice} />
          ))}
          {/* The plan as it runs, and the one question it stops to ask. Its own
              component because ChatWindow is long enough already and because
              neither half needs anything from this file but its props. */}
          <PlanProgress
            steps={steps}
            approval={approval}
            sending={sending}
            onDecide={(approved) => void decide(approved)}
          />

          {/* aria-live, because this line is the only feedback between pressing
              전송 and the answer landing, and it is never focused. The sparkle
              is the streaming indicator - the one looping animation in the app
              (§7), and it exists only while `status` does. */}
          <p aria-live="polite" className="flex items-center gap-4 text-body text-on-surface-variant">
            {status && (
              <span aria-hidden="true" className="sparkle sparkle-pulsing h-5 w-5 shrink-0" />
            )}
            {status}
          </p>
        {/* The answer itself, off screen, because a screen reader was told
            문서 검색 중… and 답변 생성 중… and then nothing at all - the status
            line is emptied the moment the answer lands, so the one thing the
            user asked for was never announced.

            A separate region rather than role="log" on the transcript above:
            that container is populated by the transcript fetch AFTER mount, so
            a live region wrapping it re-announces every message in the history
            on arrival at /chat/{id}. This one only ever changes on `done`.

            Measured in headless Edge against a stub origin: asking inside an
            existing conversation leaves this region holding the answer while
            the status line is empty. The very FIRST answer of a brand new
            conversation is the exception - router.replace reloads the document
            ~76ms later (see below) and takes this region with it, the same
            reload the answer bubble itself survives only by being refetched. */}
          <p aria-live="polite" className="sr-only">
            {announcement}
          </p>
          <p aria-live="polite" className="sr-only">
            {notice}
          </p>
          <div ref={bottomRef} />
        </div>
      </div>
      {/* No border-t. The composer is a tonal block sitting on the page, and
          the transcript above it ends where the block begins. */}
      <div className="mx-auto w-full max-w-transcript space-y-3 px-4 pb-6 sm:px-6">
        <ErrorBanner message={error} />
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={() => void handleSend()}
          onFiles={addFiles}
          attachments={attachments}
          onRemove={removeAttachment}
          sending={sending}
          onStop={() => {
            stoppedRef.current = true;
            abortRef.current?.abort();
          }}
          textareaRef={textareaRef}
          models={models}
          model={model}
          onModelChange={chooseModel}
          tools={tools}
          callables={callables}
          collectionId={collectionId}
          onCollectionChange={chooseCollection}
          toolCall={toolCall}
          onToolSelect={(call) => {
            setToolCall(call);
            setNotice(`${call.tool.name} 도구를 이번 질문에 사용합니다.`);
          }}
          onToolRemove={() => setToolCall(null)}
          orchestrator={orchestrator}
          onOrchestratorChange={chooseOrchestrator}
          mcpServers={[...new Set(tools.map((t) => t.server_name))].map((name) => ({
            name,
            on: !mcpOff.includes(name),
          }))}
          onMcpServerChange={chooseMcpServer}
          workflows={workflows}
          workflowId={workflowId}
          onWorkflowChange={chooseWorkflow}
        />
      </div>
    </div>
  );
}
