"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { MessageTrace, PlanStep, TraceEvidence, TracePlan } from "@/lib/types";

/** Why this answer looks the way it does.
 *
 * A native <dialog> + showModal(), the same pattern as CitationBadge and
 * ConfirmDialog: focus trap, Escape, an inert background and top-layer stacking
 * all come with it. Portalled to <body> because it is opened from inside the
 * transcript, where an ancestor's `overflow` would clip it.
 *
 * The cut rows are the reason this screen exists. They are rendered in the SAME
 * table as the included ones, in retrieval order, rather than in a separate
 * "dropped" section - the question being answered is "where was my document in
 * the ranking", and splitting the list destroys exactly that.
 */
function score(value: number | null, digits = 4): string {
  return value === null ? "—" : value.toFixed(digits);
}

function rank(value: number | null): string {
  // "—" for absent, not 0: a chunk the keyword search never returned has no
  // keyword rank, and a zero would read as "ranked zeroth".
  return value === null ? "—" : `${value}위`;
}

const NEIGHBOR_REASON: Record<string, string> = {
  proviso: "단서 이어붙임",
  dangling: "앞 문맥 보충",
  blanket: "일괄 확장",
};

function EvidenceRow({ item }: { item: TraceEvidence }) {
  return (
    <tr className="border-b border-outline-variant align-top">
      <td className="px-3 py-3 text-label font-medium">{item.index}</td>
      <td className="px-3 py-3">
        <div className="font-medium">{item.filename ?? item.ref}</div>
        <div className="text-caption text-on-surface-variant">
          {item.page !== null ? `${item.page}쪽` : ""}
          {item.section ? ` · ${item.section}` : ""}
          {item.source_type !== "rag" ? ` · ${item.source_type}` : ""}
        </div>
        <p className="mt-1 line-clamp-2 text-caption text-on-surface-variant">{item.snippet}</p>
        {item.neighbors.length > 0 ? (
          // The row still cites ONE chunk; this line is what stops that being a
          // lie about how much text the model actually read.
          <p className="mt-1 text-caption text-primary">
            {`앞뒤 청크 ${item.neighbors.length}개 합침 · `}
            {item.neighbors
              .map((n) => `${n.offset < 0 ? "앞" : "뒤"} #${n.chunk_index} (${NEIGHBOR_REASON[n.reason] ?? n.reason})`)
              .join(", ")}
          </p>
        ) : null}
      </td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{rank(item.vector_rank)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{rank(item.keyword_rank)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{score(item.rrf_score)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{score(item.rerank_score)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{item.tokens.toLocaleString()}</td>
      <td className="whitespace-nowrap px-3 py-3">
        {item.included ? (
          <span className="rounded-xs bg-primary-container px-2 py-1 text-caption font-medium text-on-primary-container">
            전달됨
          </span>
        ) : (
          // error-container, and the only place in this dialog that uses it:
          // nothing has failed, but this is the row that answers "why was my
          // document not used" and it has to be the thing the eye lands on.
          <span className="rounded-xs bg-error-container px-2 py-1 text-caption font-medium text-on-error-container">
            예산 초과로 제외
          </span>
        )}
      </td>
    </tr>
  );
}

/** The node kinds, for the line under a step. `rag` is not here: it is the
 * pre-Slice-6 kind for a search step, and it is rendered as one. */
const NODE_KIND_LABEL: Record<string, string> = {
  input: "질문이 들어온 자리",
  branch: "분기",
  answer: "모인 근거로 답변",
};

const PLAN_STATE_LABEL: Record<PlanStep["state"], string> = {
  running: "진행 중",
  done: "완료",
  failed: "실패",
  skipped: "건너뜀",
  timeout: "시간 초과",
};

/** The graph behind an answer, whoever authored it.
 *
 * ONE SECTION FOR BOTH. A workflow a person drew on the canvas and one 슈퍼
 * 에이전트 wrote for this question produce the same trace, and `author` is the
 * only line that differs - which is deliberate: two trace shapes would make
 * "which am I looking at" the first question on the screen instead of the last.
 *
 * The section exists for two questions the evidence table cannot answer: what
 * did it decide to do, and what did it fail to do. A refused graph is the most
 * interesting case of all - the answer came from the plain search path, and this
 * is the sentence that says why. */
function PlanSection({ plan }: { plan: TracePlan }) {
  return (
    <>
      <div className="mt-6 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-title font-medium">실행 계획</h3>
        <p className="text-caption text-on-surface-variant">
          {/* 사람 or 슈퍼 에이전트, first: every number after it means something
              different depending on who wrote the graph. */}
          {plan.author ? `${plan.author}이 만든 절차 · ` : ""}
          {plan.step_count}단계 · 도구 {plan.tool_step_count}회 · {plan.elapsed_ms.toLocaleString()}ms
        </p>
      </div>

      {plan.refused && (
        <p className="mt-3 rounded-md bg-surface-container-high p-4 text-body text-on-surface">
          계획이 거부되어 일반 문서 검색으로 답변했습니다. 사유: {plan.refused}
        </p>
      )}
      {!plan.refused && plan.step_count === 0 && (
        <p className="mt-3 rounded-md bg-surface-container-high p-4 text-body text-on-surface">
          계획 단계가 없어 일반 문서 검색으로 답변했습니다.
        </p>
      )}
      {plan.step_count > 0 && plan.fell_back_to_direct_rag && (
        <p className="mt-3 rounded-md bg-surface-container-high p-4 text-body text-on-surface">
          계획이 근거를 만들지 못해 일반 문서 검색으로 답변했습니다.
        </p>
      )}
      {plan.timed_out && (
        <p className="mt-3 rounded-md bg-surface-container-high p-4 text-body text-on-surface">
          계획 전체 제한 시간({plan.budget_seconds}초)을 넘겨 남은 단계는 실행하지 않았습니다.
        </p>
      )}

      {plan.steps.length > 0 && (
        <ol className="mt-3 space-y-2">
          {plan.steps.map((step) => (
            <li key={step.id} className="rounded-sm bg-surface-container p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="break-keep font-medium">{step.label}</span>
                <span className="shrink-0 text-caption text-on-surface-variant">
                  {PLAN_STATE_LABEL[step.state] ?? step.state} · 근거 {step.evidence_count}건 ·{" "}
                  {step.ms.toLocaleString()}ms
                </span>
              </div>
              <p className="mt-1 text-caption text-on-surface-variant">
                {/* A NODE, not a step, since Slice 6: 질문 and 답변 are nodes
                    that call nothing, and printing 컬렉션 전체 under them - which
                    this line used to do for every kind but `tool` - said the
                    answer node had searched the whole corpus. */}
                {step.kind === "tool" || step.kind === "rag"
                  ? step.tool && step.tool !== "rag"
                    ? `도구 ${step.tool} · 위험도 ${step.risk_level ?? "—"}`
                    : `문서 검색 · 분류 ${step.collections?.length ? step.collections.join(", ") : "전체"}`
                  : NODE_KIND_LABEL[step.kind] ?? step.kind}
                {step.depth ? ` · ${step.depth}단계 안쪽` : ""}
                {step.depends_on?.length ? ` · ${step.depends_on.join(", ")} 이후` : ""}
              </p>
              {step.error && <p className="mt-1 text-caption text-error">{step.error}</p>}
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-sm bg-surface-container p-3">
      <dt className="text-caption text-on-surface-variant">{label}</dt>
      <dd className="mt-1 text-body font-medium text-on-surface">{value}</dd>
    </div>
  );
}

export default function TraceDialog({
  messageId,
  onClose,
}: {
  messageId: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [trace, setTrace] = useState<MessageTrace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialogRef.current?.showModal();
    apiFetch<MessageTrace>(`/api/messages/${messageId}/trace`)
      .then(setTrace)
      .catch((err) => setError(errorMessage(err)));
  }, [messageId]);

  const cut = trace?.evidence.filter((item) => !item.included).length ?? 0;

  return createPortal(
    <dialog
      ref={dialogRef}
      aria-labelledby="trace-title"
      onClose={onClose}
      className="w-full max-w-4xl rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      <div className="max-h-[80vh] overflow-y-auto p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="trace-title" className="text-title font-medium">
              답변 추적
            </h2>
            <p className="mt-1 text-caption text-on-surface-variant">
              이 답변이 어떤 근거로 만들어졌는지, 무엇이 모델에게 전달되지 않았는지 보여줍니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            aria-label="닫기"
            className="icon-btn"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="mt-4">
          <ErrorBanner message={error} />
        </div>

        {trace === null ? (
          !error && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
        ) : (
          <>
            {/* THREE columns, not four: this grid holds nine facts now that the
                agent is one of them, and 3 divides 9 while 4 leaves a single
                orphan on the last row. */}
            <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {/* First, because it is the fact the other eight follow from: a
                  workflow decides which procedure ran, which prompt answered,
                  which corpus was in scope and which model ran. 기본 - not an em
                  dash - because "no workflow answered" is a real answer, not a
                  gap. */}
              <Fact
                label="답변 워크플로우"
                value={
                  trace.workflow_name
                    ? `${trace.workflow_name}${
                        trace.workflow_version ? ` v${trace.workflow_version}` : ""
                      }`
                    : "기본"
                }
              />
              <Fact label="답변 모델" value={trace.model ?? "—"} />
              <Fact
                label="프롬프트 버전"
                value={trace.prompt_name ? `${trace.prompt_name} v${trace.prompt_version}` : "—"}
              />
              <Fact
                label="검색 시간"
                value={trace.retrieval_ms === null ? "—" : `${trace.retrieval_ms.toLocaleString()}ms`}
              />
              <Fact
                label="생성 시간"
                value={trace.latency_ms === null ? "—" : `${trace.latency_ms.toLocaleString()}ms`}
              />
              <Fact label="RRF 상수" value={trace.retrieval.rrf_k?.toString() ?? "—"} />
              <Fact
                label="키워드 가중치"
                value={trace.retrieval.sparse_weight?.toString() ?? "—"}
              />
              <Fact
                label="토큰 예산"
                value={trace.retrieval.token_budget?.toLocaleString() ?? "—"}
              />
              {/* The pair that answers "did the prompt take my evidence". The
                  budget above is the EVIDENCE's; the system prompt is charged
                  against this allowance instead, so below it the answer is
                  always no. */}
              <Fact
                label="프롬프트 토큰"
                value={
                  typeof trace.retrieval.prompt_tokens === "number"
                    ? `${trace.retrieval.prompt_tokens.toLocaleString()} / ${trace.retrieval.mandatory_allowance?.toLocaleString() ?? "—"}`
                    : "—"
                }
              />
              <Fact
                label="사용 토큰"
                value={
                  typeof trace.usage.total_tokens === "number"
                    ? trace.usage.total_tokens.toLocaleString()
                    : "—"
                }
              />
            </dl>

            {trace.plan && <PlanSection plan={trace.plan} />}

            {!trace.has_trace ? (
              <p className="mt-6 rounded-md bg-surface-container-high p-4 text-body text-on-surface-variant">
                이 답변에는 근거 추적 정보가 없습니다. 추적 기능이 추가되기 전에 생성된 답변입니다.
              </p>
            ) : (
              <>
                <div className="mt-6 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h3 className="text-title font-medium">검색된 근거</h3>
                  <p className="text-caption text-on-surface-variant">
                    {trace.retrieval.evidence_count}개 중 {trace.retrieval.included_count}개가 모델에게
                    전달되었습니다.
                  </p>
                </div>
                {cut > 0 && (
                  // The one sentence this whole screen was built to be able to
                  // say. It is not an error banner - nothing went wrong - so it
                  // is a tonal block, per the design language's §1 and §4.
                  <p className="mt-3 rounded-md bg-surface-container-high p-4 text-body text-on-surface">
                    근거 {cut}개가 토큰 예산({trace.retrieval.token_budget?.toLocaleString()})을 넘어
                    모델에게 전달되지 않았습니다. 이 근거가 답변에 필요했다면 고급 설정에서 답변
                    컨텍스트 토큰 예산을 늘리세요.
                  </p>
                )}
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left text-body">
                    <caption className="sr-only">검색된 근거와 각 단계의 점수</caption>
                    <thead>
                      <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                        <th scope="col" className="px-3 py-3">#</th>
                        <th scope="col" className="px-3 py-3">출처</th>
                        <th scope="col" className="px-3 py-3">벡터</th>
                        <th scope="col" className="px-3 py-3">키워드</th>
                        <th scope="col" className="px-3 py-3">RRF</th>
                        <th scope="col" className="px-3 py-3">재순위</th>
                        <th scope="col" className="px-3 py-3">토큰</th>
                        <th scope="col" className="px-3 py-3">전달 여부</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trace.evidence.map((item) => (
                        <EvidenceRow key={item.index} item={item} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </dialog>,
    document.body,
  );
}
