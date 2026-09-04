"use client";

import { NODE_KIND_LABEL, conditionText } from "@/lib/graph";
import type { GraphCondition, GraphNode } from "@/lib/types";

/** 노드 설정 폼의 부품들. 인스펙터가 쓰고, JSX 없는 규칙은 lib/graph.ts에 산다. */

export const KIND_HELP: Record<GraphNode["kind"], string> = {
  input: "사용자의 질문이 여기서 들어옵니다. 그래프당 하나이며 지울 수 없습니다.",
  tool: "도구를 한 번 부릅니다. 문서 검색·MCP 도구·다른 워크플로우가 모두 여기입니다.",
  branch: "조건을 보고 나가는 간선 중 참 또는 거짓 하나를 고릅니다.",
  answer: "모인 근거로 답합니다. 그래프당 하나이며 지울 수 없습니다.",
};

/** What a tool node shows on its card without being opened. */
export function nodeDetail(node: GraphNode): string {
  if (node.kind === "tool") {
    const ref = node.tool ?? "rag";
    const query = String((node.arguments as { query?: unknown } | undefined)?.query ?? "");
    const scope = node.collections?.length ? node.collections.join(", ") : "전체 분류";
    if (ref === "rag") return `문서 검색 · ${scope}${query ? ` · ${query}` : ""}`;
    return query ? `${ref} · ${query}` : ref;
  }
  if (node.kind === "branch") return conditionText(node.condition);
  return KIND_HELP[node.kind];
}

/** A value that is a whole `{{…}}` reference, a number, a boolean or a string.
 *
 * The parse is here rather than at save because the SHAPE matters to the server:
 * `{"right": 0}` and `{"right": "0"}` compare differently, and a condition that
 * silently compares a count against the STRING "0" is refused with 분기 조건의
 * 모양이 올바르지 않습니다. at run time on somebody's question. */
export function parseLiteral(raw: string): unknown {
  const value = raw.trim();
  if (value === "") return "";
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  return value;
}

export function literalText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

/** One argument value: a reference picked from the list, or something typed.
 *
 * The two are exclusive on purpose. A reference must be the ENTIRE value - a
 * template like `"제목: {{n1.top.title}}"` is refused at save - so a control that
 * let both be true at once would be a control whose whole output the server
 * rejects. */
export function ValueField({
  id,
  label,
  value,
  options,
  onChange,
  help,
}: {
  id: string;
  label: string;
  value: unknown;
  options: { value: string; label: string }[];
  onChange: (next: unknown) => void;
  help?: string;
}) {
  const text = literalText(value);
  const isReference = text.startsWith("{{");
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-label font-medium text-on-surface-variant">
        {label}
      </label>
      <select
        id={id}
        value={isReference ? text : ""}
        onChange={(event) => onChange(event.target.value === "" ? "" : event.target.value)}
        className="field w-full"
      >
        <option value="">직접 입력</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
        {/* A reference the graph carries but this node can no longer make -
            an edge was deleted under it - stays visible rather than silently
            becoming 직접 입력 and being saved as literal text. */}
        {isReference && !options.some((o) => o.value === text) && (
          <option value={text}>{text} (지금은 이어져 있지 않음)</option>
        )}
      </select>
      {!isReference && (
        <input
          value={text}
          onChange={(event) => onChange(parseLiteral(event.target.value))}
          className="field w-full"
          placeholder="값을 직접 입력"
        />
      )}
      {help && <p className="text-caption text-on-surface-variant">{help}</p>}
    </div>
  );
}

/** The condition on a branch node. Recursive, because `and`/`or`/`not` take
 * conditions - and capped, because a person who needs four levels of nesting
 * needs a second branch node, not a deeper form. */
export function ConditionEditor({
  idPrefix,
  condition,
  options,
  onChange,
  depth = 0,
}: {
  idPrefix: string;
  condition: GraphCondition;
  options: { value: string; label: string }[];
  onChange: (next: GraphCondition) => void;
  depth?: number;
}) {
  const parts = Array.isArray(condition.of) ? (condition.of as GraphCondition[]) : [];
  return (
    <div className={depth > 0 ? "rounded-sm bg-surface-container p-3" : ""}>
      <label
        htmlFor={`${idPrefix}-kind`}
        className="block text-label font-medium text-on-surface-variant"
      >
        판단 방식
      </label>
      <select
        id={`${idPrefix}-kind`}
        value={condition.kind}
        onChange={(event) => {
          const kind = event.target.value as GraphCondition["kind"];
          if (kind === "compare") onChange({ kind, left: "", op: ">", right: 0 });
          else if (kind === "and" || kind === "or")
            onChange({ kind, of: parts.length ? parts : [{ kind: "exists", of: "" }] });
          else if (kind === "not") onChange({ kind, of: { kind: "exists", of: "" } });
          else onChange({ kind, of: "" });
        }}
        className="field mt-1 w-full"
      >
        <option value="compare">값 비교</option>
        <option value="exists">값이 있음</option>
        <option value="empty">값이 비어 있음</option>
        {depth < 2 && <option value="and">모두 참 (and)</option>}
        {depth < 2 && <option value="or">하나라도 참 (or)</option>}
        {depth < 2 && <option value="not">아님 (not)</option>}
      </select>

      {condition.kind === "compare" && (
        <div className="mt-3 space-y-3">
          <ValueField
            id={`${idPrefix}-left`}
            label="왼쪽"
            value={condition.left}
            options={options}
            onChange={(left) => onChange({ ...condition, left })}
          />
          <div>
            <label
              htmlFor={`${idPrefix}-op`}
              className="block text-label font-medium text-on-surface-variant"
            >
              비교
            </label>
            <select
              id={`${idPrefix}-op`}
              value={condition.op ?? ">"}
              onChange={(event) =>
                onChange({ ...condition, op: event.target.value as GraphCondition["op"] })
              }
              className="field mt-1 w-full"
            >
              {["==", "!=", ">", ">=", "<", "<="].map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
          </div>
          <ValueField
            id={`${idPrefix}-right`}
            label="오른쪽"
            value={condition.right}
            options={options}
            onChange={(right) => onChange({ ...condition, right })}
            help="숫자는 숫자로, true/false는 참·거짓으로 저장됩니다."
          />
        </div>
      )}

      {(condition.kind === "exists" || condition.kind === "empty") && (
        <div className="mt-3">
          <ValueField
            id={`${idPrefix}-of`}
            label="대상"
            value={condition.of}
            options={options}
            onChange={(of) => onChange({ ...condition, of })}
          />
        </div>
      )}

      {condition.kind === "not" && (
        <div className="mt-3">
          <ConditionEditor
            idPrefix={`${idPrefix}-n`}
            condition={(condition.of as GraphCondition) ?? { kind: "exists", of: "" }}
            options={options}
            depth={depth + 1}
            onChange={(of) => onChange({ kind: "not", of })}
          />
        </div>
      )}

      {(condition.kind === "and" || condition.kind === "or") && (
        <div className="mt-3 space-y-2">
          {parts.map((part, index) => (
            <div key={index} className="space-y-1">
              <ConditionEditor
                idPrefix={`${idPrefix}-${index}`}
                condition={part}
                options={options}
                depth={depth + 1}
                onChange={(next) =>
                  onChange({
                    ...condition,
                    of: parts.map((p, i) => (i === index ? next : p)),
                  })
                }
              />
              {parts.length > 1 && (
                <button
                  type="button"
                  onClick={() =>
                    onChange({ ...condition, of: parts.filter((_, i) => i !== index) })
                  }
                  className="btn-tonal btn-compact"
                >
                  이 조건 빼기
                </button>
              )}
            </div>
          ))}
          <button
            type="button"
            onClick={() => onChange({ ...condition, of: [...parts, { kind: "exists", of: "" }] })}
            className="btn-tonal btn-compact"
          >
            조건 추가
          </button>
        </div>
      )}

      {depth === 0 && (
        <p className="mt-3 text-caption text-on-surface-variant">
          모델이 판단하는 분기(kind: llm)는 스키마에 자리는 있지만 아직 켜져 있지 않습니다. 그래서
          여기에 없고, 직접 넣어 저장하면 거부됩니다 — 분기마다 모델을 부르는 비용은 소유자가 보고
          켜는 것이 맞습니다.
        </p>
      )}
    </div>
  );
}

export { NODE_KIND_LABEL };
