"use client";

import { useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import { STATUS_LABEL, TERMINAL } from "@/components/documents/DocumentTable";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { DocumentCharacter, DocumentItem } from "@/lib/types";

const CHARACTER_LABEL: Record<DocumentCharacter, string> = {
  reference_dependent: "참조형 문서",
  self_contained: "일반 문서",
};

// The 자동 판정에 맡기기 option. An <option> can only carry a string, and the
// request body carries null - which is what clears `structure.override`.
const AUTO = "";

function percent(ratio: number | undefined): string {
  return `${((ratio ?? 0) * 100).toFixed(1)}%`;
}

/** What the detector found, and what a person said about it, as Korean
 * sentences. This panel is the REASON the detector stores its counts at all:
 * this project already shipped one automatic decision nobody could see or
 * correct - a chunking strategy that selected itself by sniffing for a
 * hardcoded regex - and migration 0013 exists to undo it. An unrendered
 * inference is the failure mode, so every number that produced the verdict is
 * on screen beside it.
 *
 * See docs/superpowers/specs/2026-09-01-document-structure.md §3 and §4; the
 * sentences below are that spec's. */
export default function StructurePanel({
  doc,
  isAdmin,
  onReprocessed,
}: {
  doc: DocumentItem;
  isAdmin: boolean;
  onReprocessed: () => Promise<void>;
}) {
  const s = doc.structure ?? {};
  // Keyed on `confidence`, not on "the dict is non-empty": between 다시 처리 and
  // the worker finishing, `structure` holds the override ALONE, and reading that
  // as analysed would print 구조 인식: 일반 문서 about a document nothing has
  // looked at yet. Nothing detected is NOT "no structure found" either - the
  // collection may simply not be configured for a hierarchy.
  const analysed = s.confidence !== undefined;
  const override = s.override ?? null;
  const citations = s.citations;
  const levels = Object.entries(s.levels ?? {});

  const [choice, setChoice] = useState<string>(override ?? AUTO);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Still parsing/chunking/embedding: the panel is showing the PREVIOUS run's
  // numbers, and queueing a second job on top of the running one buys nothing.
  const working = !TERMINAL.has(doc.status);

  async function reprocess() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch<DocumentItem>(`/api/documents/${doc.id}/reprocess`, {
        method: "POST",
        body: JSON.stringify({ character: choice === AUTO ? null : choice }),
      });
      await onReprocessed();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const applied = s.character ?? "self_contained";
  const title = override
    ? CHARACTER_LABEL[override]
    : s.confidence === "high"
      ? CHARACTER_LABEL[applied]
      : s.confidence === "ambiguous"
        ? "판단 보류"
        : "일반 문서";

  return (
    <section className="space-y-3 rounded-md bg-surface-container-low p-4">
      <h2 className="text-title font-medium text-on-surface">구조 인식</h2>

      {!analysed ? (
        <div className="space-y-1.5 text-body text-on-surface">
          <p>구조 인식: 아직 분석되지 않았습니다.</p>
          {override && (
            <p className="text-on-surface-variant">사람이 지정: {CHARACTER_LABEL[override]}</p>
          )}
        </div>
      ) : (
        <div className="space-y-1.5 text-body text-on-surface">
          <p>
            <span className="font-medium">구조 인식: {title}</span>{" "}
            <span className="text-on-surface-variant">
              {override ? "(사람이 지정)" : s.confidence === "high" ? "(자동 판정, 확신 높음)" : null}
            </span>
          </p>

          {/* Both numbers, always, when somebody has disagreed with the detector.
              `detected` is stored separately from `character` for exactly this
              line - "자동은 뭐라고 했고 사람은 뭐라고 했는지"가 항상 같이 읽혀야 한다. */}
          {override && s.detected && (
            <p className="text-on-surface-variant">
              자동 판정: {CHARACTER_LABEL[s.detected]} / 사람이 지정: {CHARACTER_LABEL[override]}
            </p>
          )}

          {s.confidence === "ambiguous" && (
            <>
              <p>
                계층 표시는 {percent(s.spine_ratio)}, 인용은 {percent(s.citation_ratio)} 의 문단에서만
                보입니다.
              </p>
              {!override && (
                <p>
                  참조형 문서로 보기에는 약해서 일반 문서로 처리했습니다.
                  {/* The spec's 3rd sentence points at a control. Only an admin
                      has one below, so a reader who cannot act is not sent to
                      a button that is not on their screen. */}
                  {isAdmin && " 이 문서가 법령·규정이라면 아래에서 참조형 문서로 바꾸고 다시 처리해 주세요."}
                </p>
              )}
            </>
          )}

          {s.confidence === "none" && (
            <p>
              계층 표시(장·조·항)를 찾지 못했습니다.
              {!override && " 기존 방식으로 처리합니다."}
            </p>
          )}

          {s.confidence !== "none" && levels.length > 0 && (
            <p>
              {levels.map(([name, count]) => `${name} ${count.toLocaleString()}개`).join(" · ")}를
              찾았습니다.
            </p>
          )}

          {citations && citations.found > 0 && (
            <p>
              인용 {citations.found.toLocaleString()}건 중 {citations.resolved.toLocaleString()}건을
              문서 안에서 해소했습니다. (미해소 {citations.unresolved.toLocaleString()}건)
            </p>
          )}

          {/* §4(c). The hierarchy is still valid, so nothing is turned off - but
              "resolved 0" has one likely cause and the screen says it rather
              than leaving a zero to be read as a bug. */}
          {citations && citations.found > 0 && citations.resolved === 0 && (
            <p>인용 대상 문서(예: 특허법)가 아직 등록되지 않았을 수 있습니다.</p>
          )}

          {/* Verbatim, not counted: `[민법950]` is what tells the reader WHICH
              law this corpus is missing, and "189건 미해소" does not. */}
          {(s.unresolved_examples ?? []).length > 0 && (
            <p className="flex flex-wrap items-center gap-1.5 pt-0.5">
              <span className="text-on-surface-variant">미해소 인용 예:</span>
              {(s.unresolved_examples ?? []).map((example) => (
                <span
                  key={example}
                  className="rounded-xs bg-surface-container-high px-1.5 py-0.5 font-mono text-caption text-on-surface"
                >
                  {example}
                </span>
              ))}
            </p>
          )}
        </div>
      )}

      {isAdmin && (
        <div className="flex flex-wrap items-end gap-2 pt-1">
          <div className="flex flex-col gap-1">
            <label htmlFor="structure-character" className="text-body text-on-surface-variant">
              성격 바꾸기
            </label>
            <select
              id="structure-character"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
              className="field px-2"
            >
              <option value="reference_dependent">참조형 문서</option>
              <option value="self_contained">일반 문서</option>
              <option value={AUTO}>자동 판정에 맡기기</option>
            </select>
          </div>
          <button type="button" onClick={() => void reprocess()} disabled={busy || working} className="btn-tonal">
            {busy ? "요청 중..." : "다시 처리"}
          </button>
          {working && (
            <span className="pb-2.5 text-caption text-on-surface-variant">
              지금 {STATUS_LABEL[doc.status] ?? doc.status}입니다.
            </span>
          )}
        </div>
      )}

      <ErrorBanner message={error} />
    </section>
  );
}
