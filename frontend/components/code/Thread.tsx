"use client";

import Markdown from "@/components/chat/Markdown";
import { TOOL_LABEL, toolSummary, type OcMessage, type OcPart } from "@/lib/code";

/** 세션의 대화 - 사용자 말풍선과, 에이전트의 파트(글·생각·도구 호출·패치)를 도착한 순서대로. */
/** compact(간편 모드): 도구 호출·생각을 "작업 과정 N단계" 하나로 접는다 - 라이트 사용자는 결과만 보면 된다. */
export default function Thread({ messages, compact = false }: { messages: OcMessage[]; compact?: boolean }) {
  const ordered = [...messages].sort((a, b) => a.info.time.created - b.info.time.created);
  return (
    <div className="space-y-4">
      {ordered.map((m) => (m.info.role === "user" ? <UserMessage key={m.info.id} message={m} /> : <AssistantMessage key={m.info.id} message={m} compact={compact} />))}
    </div>
  );
}

function UserMessage({ message }: { message: OcMessage }) {
  const text = message.parts
    .filter((p) => p.type === "text" && !("synthetic" in p && (p as { synthetic?: boolean }).synthetic))
    .map((p) => p.text ?? "")
    .join("\n");
  if (!text) return null;
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-primary-container px-4 py-2 text-body text-on-primary-container">{text}</div>
    </div>
  );
}

function AssistantMessage({ message, compact }: { message: OcMessage; compact: boolean }) {
  const err = message.info.error;
  const steps = message.parts.filter((p) => p.type === "tool" || p.type === "reasoning" || p.type === "patch");
  const texts = message.parts.filter((p) => p.type === "text");
  const running = steps.some((p) => p.type === "tool" && (p.state?.status === "running" || p.state?.status === "pending"));
  return (
    <div className="space-y-2">
      {compact ? (
        <>
          {steps.length > 0 && (
            <details className="rounded-md bg-surface-container-low px-3 py-2 text-caption text-on-surface-variant">
              <summary className="cursor-pointer">
                {running ? "작업 중 · " : "작업 과정 · "}
                {steps.filter((p) => p.type === "tool").length}단계
              </summary>
              <div className="mt-2 space-y-2">
                {steps.map((p) => (
                  <Part key={p.id} part={p} />
                ))}
              </div>
            </details>
          )}
          {texts.map((p) => (
            <Part key={p.id} part={p} />
          ))}
        </>
      ) : (
        message.parts.map((p) => <Part key={p.id} part={p} />)
      )}
      {err && err.name !== "MessageAbortedError" && (
        <div role="alert" className="rounded-md bg-error-container px-4 py-3 text-body text-on-error-container">
          {err.data?.message || err.name}
        </div>
      )}
      {err?.name === "MessageAbortedError" && <p className="text-caption text-on-surface-variant">중단했습니다.</p>}
    </div>
  );
}

function Part({ part }: { part: OcPart }) {
  switch (part.type) {
    case "text":
      return part.text ? (
        <div className="markdown text-body text-on-surface">
          <Markdown content={part.text} citations={[]} />
        </div>
      ) : null;
    case "reasoning":
      return part.text ? (
        <details className="rounded-md bg-surface-container-low px-3 py-2 text-caption text-on-surface-variant">
          <summary className="cursor-pointer">생각</summary>
          <pre className="mt-2 whitespace-pre-wrap break-words">{part.text}</pre>
        </details>
      ) : null;
    case "tool":
      return <ToolCard part={part} />;
    case "patch":
      return part.files && part.files.length > 0 ? (
        <p className="text-caption text-on-surface-variant">변경: {part.files.join(", ")}</p>
      ) : null;
    default:
      // step-start · step-finish · snapshot 등은 화면에 그릴 것이 없다.
      return null;
  }
}

const STATUS_LABEL: Record<string, string> = { pending: "준비", running: "실행 중", completed: "완료", error: "실패" };

function ToolCard({ part }: { part: OcPart }) {
  const st = part.state;
  const status = st?.status ?? "pending";
  const summary = toolSummary(part);
  const output = status === "error" ? st?.error : st?.output;
  const tone = status === "error" ? "bg-error-container text-on-error-container" : "bg-surface-container-high text-on-surface";
  return (
    <details className={`rounded-md ${tone}`}>
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-caption">
        <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${status === "running" ? "animate-pulse bg-primary" : status === "error" ? "bg-error" : "bg-outline"}`} aria-hidden="true" />
        <span className="shrink-0 font-medium">{TOOL_LABEL[part.tool ?? ""] ?? part.tool}</span>
        <span className="min-w-0 flex-1 truncate font-mono">{summary}</span>
        <span className="shrink-0 text-on-surface-variant">{STATUS_LABEL[status] ?? status}</span>
      </summary>
      {output && (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all px-3 pb-3 text-caption">{output.length > 20000 ? output.slice(0, 20000) + "\n…(잘림)" : output}</pre>
      )}
    </details>
  );
}
