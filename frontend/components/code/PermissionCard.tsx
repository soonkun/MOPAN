"use client";

import { PERMISSION_LABEL, type Permission, type Question } from "@/lib/code";

/** 에이전트가 멈춰 서서 묻는 것 - 명령 실행·파일 수정·웹 읽기. Claude Code의 권한 프롬프트와 같은 세 답:
 * 이번만 / 항상(이 세션에서 같은 패턴은 다시 묻지 않음) / 거부. */
export function PermissionCard({ permission, onReply }: { permission: Permission; onReply: (reply: "once" | "always" | "reject") => void }) {
  const meta = permission.metadata ?? {};
  const detail =
    (typeof meta.command === "string" && meta.command) ||
    (typeof meta.filePath === "string" && meta.filePath) ||
    (typeof meta.url === "string" && meta.url) ||
    permission.patterns.join(", ");
  return (
    <div role="group" aria-label="권한 요청" className="rounded-md bg-primary-container p-4 text-on-primary-container">
      <p className="text-label font-medium">{PERMISSION_LABEL[permission.permission] ?? `${permission.permission} 권한을 요청합니다`}</p>
      {detail && <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded-sm bg-surface px-3 py-2 text-caption text-on-surface">{detail}</pre>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="btn-filled btn-compact" onClick={() => onReply("once")}>
          이번만 허용
        </button>
        <button type="button" className="btn-tonal btn-compact" onClick={() => onReply("always")}>
          항상 허용
        </button>
        <button type="button" className="btn-text btn-compact" onClick={() => onReply("reject")}>
          거부
        </button>
      </div>
    </div>
  );
}

/** 에이전트의 되물음(question 도구). 선택지가 있으면 버튼, 없으면 입력칸. */
export function QuestionCard({ question, onAnswer, onReject }: { question: Question; onAnswer: (answers: string[][]) => void; onReject: () => void }) {
  const items = question.questions ?? [];
  return (
    <div role="group" aria-label="질문" className="rounded-md bg-surface-container-high p-4">
      {items.map((q, i) => (
        <div key={i} className={i > 0 ? "mt-3" : ""}>
          {q.header && <p className="text-caption text-on-surface-variant">{q.header}</p>}
          <p className="text-body text-on-surface">{q.question}</p>
          {q.options && q.options.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {q.options.map((o) => (
                <button
                  key={o.label}
                  type="button"
                  title={o.description}
                  className="btn-tonal btn-compact"
                  onClick={() => onAnswer(items.map((_, j) => (j === i ? [o.label] : [])))}
                >
                  {o.label}
                </button>
              ))}
            </div>
          ) : (
            <form
              className="mt-2 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const v = new FormData(e.currentTarget).get("a");
                onAnswer(items.map((_, j) => (j === i ? [String(v ?? "")] : [])));
              }}
            >
              <input name="a" className="field flex-1" placeholder="답을 입력" />
              <button type="submit" className="btn-filled btn-compact">
                보내기
              </button>
            </form>
          )}
        </div>
      ))}
      <button type="button" className="btn-text btn-compact mt-3" onClick={onReject}>
        건너뛰기
      </button>
    </div>
  );
}
