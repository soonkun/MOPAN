"use client";

import type { ApprovalRequest, PlanStep } from "@/lib/types";

/** What the Super Agent is doing, and the one question it stops to ask.
 *
 * Two things rather than two components because they are one region of the
 * transcript and never both interesting at once: while the plan runs the step
 * list is the whole story, and the moment it pauses the card is.
 *
 * The step list is a tonal block, not a banner - nothing has gone wrong, and §1
 * and §4 of the design language say hierarchy comes from surface tone. The
 * approval card is the one place in the chat that uses the error tokens.
 * Nothing has failed there either, but it is a destructive action asking for a
 * person, and it has to be the thing the eye lands on. */

// `running` deliberately has no entry: the step's own label beside a live
// sparkle is what "in progress" looks like, and a second word for it would be
// noise on every row.
const STATE_LABEL: Record<string, string> = {
  done: "완료",
  failed: "실패",
  skipped: "건너뜀",
  timeout: "시간 초과",
};

const RISK_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};

function StateIcon({ state }: { state: PlanStep["state"] }) {
  if (state === "running") {
    return <span aria-hidden="true" className="sparkle sparkle-pulsing mt-0.5 block h-4 w-4 shrink-0" />;
  }
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={`mt-0.5 h-4 w-4 shrink-0 ${state === "done" ? "text-primary" : "text-on-surface-variant"}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      {state === "done" ? <path d="M5 13l4 4L19 7" /> : <path d="M6 6l12 12M18 6L6 18" />}
    </svg>
  );
}

export default function PlanProgress({
  steps,
  approval,
  sending,
  onDecide,
}: {
  steps: PlanStep[];
  approval: ApprovalRequest | null;
  sending: boolean;
  onDecide: (approved: boolean) => void;
}) {
  return (
    <>
      {steps.length > 0 && (
        <ol aria-label="실행 계획" className="space-y-2 rounded-md bg-surface-container-low p-4">
          {steps.map((step) => (
            <li key={step.id} className="flex items-start gap-3 text-body">
              <StateIcon state={step.state} />
              <span className="min-w-0 flex-1 break-keep text-on-surface">{step.label}</span>
              <span className="shrink-0 text-caption text-on-surface-variant">
                {step.state === "running"
                  ? "…"
                  : step.state === "done"
                    ? `${STATE_LABEL.done} · 근거 ${step.evidence_count}건`
                    : (step.detail ?? STATE_LABEL[step.state] ?? step.state)}
              </span>
            </li>
          ))}
        </ol>
      )}

      {approval && (
        <div
          role="group"
          aria-labelledby="approval-title"
          className="rounded-md bg-error-container p-4 text-on-error-container"
        >
          <h2 id="approval-title" className="text-title font-medium">
            도구 실행을 승인하시겠습니까?
          </h2>
          <p className="mt-2 break-keep text-body">
            실행 계획이 <strong>{approval.step.server}</strong> 서버의{" "}
            <strong>{approval.step.tool}</strong> 도구를 호출하려고 합니다. 위험도는{" "}
            {RISK_LABEL[approval.step.risk_level] ?? approval.step.risk_level}입니다. 승인하기 전까지
            이 도구는 실행되지 않습니다.
          </p>
          {Object.keys(approval.step.arguments).length > 0 && (
            <dl className="mt-3 space-y-1 text-caption">
              {Object.entries(approval.step.arguments).map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <dt className="font-medium">{key}</dt>
                  <dd className="min-w-0 break-all">{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onDecide(true)}
              disabled={sending}
              // NOT .btn-danger, which is error-CONTAINER on on-error-container -
              // the same fill as the card it sits in, so it rendered as bare text
              // with no button shape at all. Seen in a screenshot, not in the
              // markup. The filled error/on-error pair is what reads as a button
              // here, in both themes.
              className="inline-flex h-10 items-center justify-center rounded-sm bg-error px-4 text-label font-medium text-on-error transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
            >
              승인하고 실행
            </button>
            <button
              type="button"
              onClick={() => onDecide(false)}
              disabled={sending}
              className="h-10 rounded-sm border border-on-error-container px-4 text-label font-medium text-on-error-container transition-opacity duration-150 hover:opacity-80 disabled:opacity-50"
            >
              이 단계 없이 계속
            </button>
          </div>
        </div>
      )}
    </>
  );
}
