"use client";

import { useId } from "react";
import type { AnswerModel, GraphCategory, GraphField, GraphNode, GraphNodeConfig } from "@/lib/types";

/** 모델·텍스트 노드(모델 호출·질문 분류·정보 추출·텍스트 조합)의 설정 폼.
 * 본문은 템플릿이라 `{{노드.항목}}`을 글 안에 섞어 쓴다 - 아래 '참조 넣기'가 그것을 커서
 * 자리에 꽂는다. 규칙은 서버(graph.py:_parse_config)가 저장 때 검사한다. */

const ID_RE = /^[\w가-힣-]{1,40}$/u;

function ReferencePicker({ options, onPick }: { options: { value: string; label: string }[]; onPick: (v: string) => void }) {
  if (options.length === 0) return null;
  return (
    <select
      defaultValue=""
      onChange={(e) => {
        if (e.target.value) onPick(e.target.value);
        e.target.value = "";
      }}
      className="field h-8 text-caption"
      aria-label="참조 넣기"
    >
      <option value="" disabled>
        참조 넣기…
      </option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function TemplateArea({
  id,
  label,
  value,
  rows,
  options,
  onChange,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  rows: number;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={id} className="text-caption text-on-surface-variant">
          {label}
        </label>
        <ReferencePicker
          options={options}
          onPick={(ref) => {
            const el = document.getElementById(id) as HTMLTextAreaElement | null;
            const start = el?.selectionStart ?? value.length;
            const end = el?.selectionEnd ?? value.length;
            onChange(value.slice(0, start) + ref + value.slice(end));
            requestAnimationFrame(() => el?.focus());
          }}
        />
      </div>
      <textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={rows}
        placeholder={placeholder}
        spellCheck={false}
        className="field mt-1 w-full font-mono text-[13px]"
      />
    </div>
  );
}

function ModelSelect({ value, models, onChange }: { value: string | null | undefined; models: AnswerModel[]; onChange: (v: string | null) => void }) {
  return (
    <label className="block">
      <span className="text-caption text-on-surface-variant">모델</span>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} className="field mt-1 w-full">
        <option value="">기본 답변 모델</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label}
            {m.provider === "local" ? " (로컬 GPU)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function ConfigEditor({
  node,
  options,
  models,
  onChange,
}: {
  node: GraphNode;
  options: { value: string; label: string }[];
  models: AnswerModel[];
  onChange: (config: GraphNodeConfig) => void;
}) {
  const uid = useId();
  const c: GraphNodeConfig = node.config ?? {};
  const set = (patch: Partial<GraphNodeConfig>) => onChange({ ...c, ...patch });

  if (node.kind === "template") {
    return (
      <TemplateArea id={`${uid}-text`} label="본문 - 앞 노드의 값을 {{노드.항목}}으로 섞어 씁니다" value={c.text ?? ""} rows={6} options={options} onChange={(text) => set({ text })} />
    );
  }

  if (node.kind === "llm") {
    const fields = c.output_fields ?? [];
    return (
      <div className="space-y-3">
        <TemplateArea id={`${uid}-system`} label="역할·규칙 (system, 선택)" value={c.system ?? ""} rows={3} options={options} onChange={(system) => set({ system })} placeholder="예) 당신은 특허 심사 보조원입니다. 두 문장으로 답하세요." />
        <TemplateArea id={`${uid}-prompt`} label="프롬프트 (user)" value={c.prompt ?? ""} rows={6} options={options} onChange={(prompt) => set({ prompt })} placeholder="예) 다음 질문을 검색어 한 줄로 다시 써 주세요: {{input.text}}" />
        <ModelSelect value={c.model} models={models} onChange={(model) => set({ model })} />
        <label className="block">
          <span className="text-caption text-on-surface-variant">
            구조화 출력 항목 (선택, 쉼표로) - 적으면 JSON으로 답하게 하고 {"{{"}이노드.항목{"}}"}으로 읽습니다
          </span>
          <input
            value={fields.join(", ")}
            onChange={(e) =>
              set({ output_fields: e.target.value.split(",").map((s) => s.trim()).filter((s) => ID_RE.test(s)) })
            }
            placeholder="예) query, reason"
            className="field mt-1 w-full"
          />
        </label>
        <label className="flex items-center gap-2 text-body">
          <input type="checkbox" checked={Boolean(c.as_evidence)} onChange={(e) => set({ as_evidence: e.target.checked })} />
          결과를 답변 근거로도 넘긴다
        </label>
        <p className="text-caption text-on-surface-variant">결과는 {"{{"}{node.id}.text{"}}"}. 끄면 다음 노드만 읽고, 켜면 답변 모델이 근거 [n]으로도 봅니다.</p>
      </div>
    );
  }

  if (node.kind === "classify") {
    const categories = c.categories ?? [];
    const update = (i: number, patch: Partial<GraphCategory>) =>
      set({ categories: categories.map((cat, j) => (j === i ? { ...cat, ...patch } : cat)) });
    return (
      <div className="space-y-3">
        <TemplateArea id={`${uid}-text`} label="분류할 글" value={c.text ?? "{{input.text}}"} rows={2} options={options} onChange={(text) => set({ text })} />
        <div>
          <span className="text-caption text-on-surface-variant">갈래 (2~12개) - id는 간선 이름, 설명은 모델이 고를 때 읽습니다</span>
          <ul className="mt-1 space-y-2">
            {categories.map((cat, i) => (
              <li key={i} className="rounded-sm bg-surface-container p-2">
                <div className="flex gap-2">
                  <input value={cat.id} onChange={(e) => update(i, { id: e.target.value.replace(/[^\w가-힣-]/gu, "").slice(0, 40) })} placeholder="id" aria-label="갈래 id" className="field h-8 w-24 text-caption" />
                  <input value={cat.label} onChange={(e) => update(i, { label: e.target.value })} placeholder="표시 이름" aria-label="갈래 이름" className="field h-8 min-w-0 flex-1 text-caption" />
                  <button type="button" onClick={() => set({ categories: categories.filter((_, j) => j !== i) })} disabled={categories.length <= 2} className="btn-text btn-compact" aria-label="갈래 제거">
                    ×
                  </button>
                </div>
                <input value={cat.description ?? ""} onChange={(e) => update(i, { description: e.target.value })} placeholder="어떤 질문이 여기로 오나 (예: 법령·규정의 조항을 묻는 질문)" aria-label="갈래 설명" className="field mt-1 h-8 w-full text-caption" />
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => set({ categories: [...categories, { id: `c${categories.length + 1}`, label: "", description: "" }] })}
            disabled={categories.length >= 12}
            className="btn-tonal btn-compact mt-2"
          >
            + 갈래
          </button>
        </div>
        <ModelSelect value={c.model} models={models} onChange={(model) => set({ model })} />
        <p className="text-caption text-on-surface-variant">
          고른 갈래는 {"{{"}{node.id}.category{"}}"}. 나가는 간선마다 갈래를 지정하고, 갈래마다 최소 한 간선을 이어 두세요 - 잇지 않은 갈래로 가면 그 뒤는 실행되지 않습니다.
        </p>
      </div>
    );
  }

  // extract
  const fields = c.fields ?? [];
  const updateField = (i: number, patch: Partial<GraphField>) => set({ fields: fields.map((f, j) => (j === i ? { ...f, ...patch } : f)) });
  return (
    <div className="space-y-3">
      <TemplateArea id={`${uid}-text`} label="읽을 글" value={c.text ?? "{{input.text}}"} rows={2} options={options} onChange={(text) => set({ text })} />
      <div>
        <span className="text-caption text-on-surface-variant">뽑을 항목 (1~12개) - 이름이 곧 {"{{"}{node.id}.이름{"}}"}</span>
        <ul className="mt-1 space-y-2">
          {fields.map((f, i) => (
            <li key={i} className="rounded-sm bg-surface-container p-2">
              <div className="flex gap-2">
                <input value={f.name} onChange={(e) => updateField(i, { name: e.target.value.replace(/[^\w가-힣-]/gu, "").slice(0, 40) })} placeholder="이름" aria-label="항목 이름" className="field h-8 w-28 text-caption" />
                <select value={f.type} onChange={(e) => updateField(i, { type: e.target.value as GraphField["type"] })} aria-label="항목 형" className="field h-8 text-caption">
                  <option value="string">글</option>
                  <option value="number">수</option>
                  <option value="boolean">예/아니오</option>
                </select>
                <button type="button" onClick={() => set({ fields: fields.filter((_, j) => j !== i) })} disabled={fields.length <= 1} className="btn-text btn-compact" aria-label="항목 제거">
                  ×
                </button>
              </div>
              <input value={f.description ?? ""} onChange={(e) => updateField(i, { description: e.target.value })} placeholder="설명 (예: 지역명. 시·군 단위)" aria-label="항목 설명" className="field mt-1 h-8 w-full text-caption" />
            </li>
          ))}
        </ul>
        <button type="button" onClick={() => set({ fields: [...fields, { name: `f${fields.length + 1}`, type: "string", description: "" }] })} disabled={fields.length >= 12} className="btn-tonal btn-compact mt-2">
          + 항목
        </button>
      </div>
      <ModelSelect value={c.model} models={models} onChange={(model) => set({ model })} />
      <p className="text-caption text-on-surface-variant">글에 없는 항목은 비워 둡니다(지어내지 않음). 도구 노드의 인자에 {"{{"}{node.id}.이름{"}}"}으로 넣어 쓰세요.</p>
    </div>
  );
}
