"use client";

import { useRef, useState } from "react";
import { apiFetch, streamChat } from "@/lib/api";
import type { ChatEvent } from "@/lib/types";

/** 실행해보기 - 만든 워크플로우를 편집기 안에서 바로 굴려 본다(소유자 요청:
 * "테스트를 여기서 해 봐야지"). 채팅과 완전히 같은 경로(POST /api/chat +
 * workflow_id)를 타므로 여기서 되는 것은 채팅에서도 된다 - 전용 dry-run
 * 경로를 만들면 그 보장이 깨진다.
 *
 * 실행마다 서버에 대화가 생기는데, 테스트가 대화 기록을 어지럽히면 안 되므로
 * 결과를 다 받은 뒤 그 대화를 지운다. 삭제 실패는 조용히 넘어간다 - 기록에
 * 한 줄 남는 것이 실행 결과를 잃는 것보다 낫다. */

const STATUS_LABEL: Record<string, string> = {
  planning: "실행 계획 세우는 중…",
  calling_tool: "도구 호출 중…",
  searching: "문서 검색 중…",
  answering: "답변 생성 중…",
};

export default function TestRun({
  workflowId,
  onClose,
}: {
  workflowId: string;
  onClose: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  const [answer, setAnswer] = useState<string | null>(null);
  const [citations, setCitations] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function run() {
    if (!question.trim() || running) return;
    setRunning(true);
    setSteps([]);
    setAnswer(null);
    setCitations(0);
    setError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    let conversationId: string | null = null;
    try {
      await streamChat(
        { message: question, workflow_id: workflowId },
        (event: ChatEvent) => {
          if (event.type === "status") {
            const label = STATUS_LABEL[event.status] ?? event.status;
            setSteps((prev) => (prev[prev.length - 1] === label ? prev : [...prev, label]));
          } else if (event.type === "step") {
            // 그래프 실행의 단계 프레임: running/done을 그대로 시간순으로.
            setSteps((prev) => [...prev, `${event.label || event.id} — ${event.state}`]);
          } else if (event.type === "done") {
            conversationId = event.conversation_id;
            setAnswer(event.content);
            setCitations((event.citations ?? []).length);
          } else if (event.type === "error") {
            setError(event.detail);
          }
        },
        controller.signal,
      );
    } catch (err) {
      if ((err as { name?: string } | null)?.name !== "AbortError") {
        setError(err instanceof Error ? err.message : "실행에 실패했습니다.");
      }
    } finally {
      setRunning(false);
      if (conversationId) {
        // 테스트는 기록에 남기지 않는다.
        void apiFetch(`/api/conversations/${conversationId}`, { method: "DELETE" }).catch(
          () => undefined,
        );
      }
    }
  }

  return (
    <aside
      aria-label="실행해보기"
      className="pointer-events-auto absolute bottom-3 right-3 top-3 z-20 flex w-96 max-w-[calc(100%-1.5rem)] flex-col rounded-md bg-surface-container shadow-menu"
    >
      <div className="flex items-center justify-between gap-2 p-3 pb-2">
        <h2 className="text-label font-medium text-on-surface">실행해보기</h2>
        <button
          type="button"
          onClick={() => {
            abortRef.current?.abort();
            onClose();
          }}
          className="icon-btn h-7 w-7"
          aria-label="실행해보기 닫기"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
      <p className="px-3 pb-2 text-caption text-on-surface-variant">
        저장된 그래프를 채팅과 같은 경로로 실행합니다. 테스트 대화는 기록에 남지 않습니다.
      </p>
      <div className="flex gap-2 px-3 pb-3">
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void run();
          }}
          placeholder="예) 살충제 살포 시 주의사항은?"
          aria-label="테스트 질문"
          className="field h-9 min-w-0 flex-1 text-body"
        />
        <button
          type="button"
          onClick={() => void run()}
          disabled={running || !question.trim()}
          className="btn-filled btn-compact shrink-0"
        >
          {running ? "실행 중..." : "실행"}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {steps.length > 0 && (
          <ol className="mb-3 space-y-1">
            {steps.map((step, index) => (
              <li key={index} className="text-caption text-on-surface-variant">
                {step}
              </li>
            ))}
          </ol>
        )}
        {error && (
          <p className="rounded-sm bg-error-container px-3 py-2 text-caption text-on-error-container">
            {error}
          </p>
        )}
        {answer !== null && (
          <div className="rounded-md bg-surface-container-low p-3">
            <p className="whitespace-pre-wrap text-body text-on-surface">{answer}</p>
            <p className="mt-2 text-caption text-on-surface-variant">인용 {citations}건</p>
          </div>
        )}
      </div>
    </aside>
  );
}
