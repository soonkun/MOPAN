"use client";

import { useState } from "react";
import type { CallableTool, GraphNode } from "@/lib/types";

/** The tool drawer. Collapsed by default into a slim rail, because the canvas
 * is the protagonist of this screen and the drawer is a box of pens - you open
 * it, take one, and it gets out of the way.
 *
 * Two ways to take a tool, deliberately: CLICK adds the node at the centre of
 * the current view (the keyboard/no-pointer route), DRAG drops it where the
 * pointer lands. Both call the same `onAdd`; only the position differs.
 */

const NODE_ITEMS: { kind: GraphNode["kind"]; label: string; hint: string }[] = [
  { kind: "tool", label: "도구", hint: "문서 검색·MCP·워크플로우 호출" },
  { kind: "llm", label: "모델 호출", hint: "프롬프트로 모델을 부르고 글을 받음" },
  { kind: "classify", label: "질문 분류", hint: "갈래 중 하나로 분류해 길을 고름" },
  { kind: "extract", label: "정보 추출", hint: "글에서 항목을 뽑아 다음 노드에" },
  { kind: "template", label: "텍스트 조합", hint: "앞 노드 값들을 글 하나로" },
  { kind: "branch", label: "분기", hint: "조건으로 참·거짓 갈래" },
];

function kindOf(callable: CallableTool): string {
  if (callable.kind === "rag") return "문서 검색";
  if (callable.kind === "workflow") return "워크플로우";
  return "MCP";
}

export default function Palette({
  callables,
  onAdd,
  open,
  onOpenChange,
}: {
  callables: CallableTool[];
  /** tool이 undefined면 맨 노드(도구/분기), 있으면 그 도구가 미리 채워진
   * 도구 노드. at은 팔레트에서 끌어다 놓은 뷰포트 좌표 - 없으면 화면 중앙. */
  onAdd: (kind: GraphNode["kind"], tool?: string, at?: { clientX: number; clientY: number }) => void;
  /** 캔버스가 들고 있다 - 처음 화면 맞추기가 서랍 폭을 알아야 해서. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const setOpen = onOpenChange;

  const filtered = callables.filter(
    (c) =>
      !query.trim() ||
      c.name.toLowerCase().includes(query.trim().toLowerCase()) ||
      c.ref.toLowerCase().includes(query.trim().toLowerCase()),
  );

  // 접힌 버튼은 페이지의 왼쪽 레일이 그린다(워크플로우 설정·도구·노드 순).
  if (!open) return null;

  return (
    <div className="pointer-events-auto absolute inset-x-0 bottom-[3.75rem] top-2 z-10 flex flex-col rounded-t-xl bg-surface-container shadow-dialog sm:inset-x-auto sm:bottom-3 sm:left-3 sm:top-[9.75rem] sm:max-h-none sm:w-72 sm:max-w-[calc(100%-1.5rem)] sm:rounded-md sm:shadow-menu">
      <span className="mx-auto mt-2 h-1 w-10 shrink-0 rounded-full bg-outline-variant sm:hidden" aria-hidden="true" />
      <div className="flex items-center justify-between gap-2 p-3 pb-2">
        <h2 className="text-label font-medium text-on-surface">도구 서랍</h2>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="icon-btn h-7 w-7"
          aria-label="도구 서랍 접기"
          aria-expanded="true"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="m15 6-6 6 6 6" />
          </svg>
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <p className="px-3 pb-2 text-caption text-on-surface-variant">
          누르면 캔버스 가운데에 생기고, 데스크톱에선 끌어다 놓을 수도 있습니다.
        </p>
        <h3 className="px-3 pb-1 pt-1 text-caption font-medium uppercase tracking-wide text-on-surface-variant">노드</h3>
        <ul className="space-y-0.5 px-2 pb-2">
          {NODE_ITEMS.map((item) => (
            <li key={item.kind}>
              <button
                type="button"
                draggable
                onClick={() => onAdd(item.kind)}
                onDragStart={(event) => {
                  event.dataTransfer.setData("application/x-mopan-node", item.kind);
                  event.dataTransfer.effectAllowed = "copy";
                }}
                className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-surface-container-high"
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-surface-container-highest text-on-surface" aria-hidden="true">
                  <NodeGlyph kind={item.kind} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-body font-medium text-on-surface">{item.label}</span>
                  <span className="block truncate text-caption text-on-surface-variant">{item.hint}</span>
                </span>
                <span className="shrink-0 text-caption text-primary">추가</span>
              </button>
            </li>
          ))}
        </ul>

        <h3 className="px-3 pb-1 pt-2 text-caption font-medium uppercase tracking-wide text-on-surface-variant">부를 수 있는 것</h3>
        <div className="px-3 pb-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="문서 검색·MCP 도구·워크플로우 찾기"
            className="field h-9 w-full text-body"
            aria-label="부를 수 있는 것 찾기"
          />
        </div>
        <ul className="space-y-0.5 px-2 pb-3">
        {filtered.length === 0 && (
          <li className="px-2 py-2 text-caption text-on-surface-variant">
            {callables.length === 0
              ? "부를 수 있는 것이 없습니다. MCP 서버가 없는 배포에서는 문서 검색만 뜹니다."
              : "이름에 맞는 것이 없습니다."}
          </li>
        )}
        {filtered.map((callable) => (
          <li key={callable.ref}>
            <button
              type="button"
              draggable
              onClick={() => onAdd("tool", callable.ref)}
              onDragStart={(event) => {
                event.dataTransfer.setData("application/x-mopan-tool", callable.ref);
                event.dataTransfer.effectAllowed = "copy";
              }}
              className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left hover:bg-surface-container-high"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body text-on-surface">{callable.name}</span>
                <span className="block truncate text-caption text-on-surface-variant">
                  {kindOf(callable)}
                  {callable.description ? ` · ${callable.description}` : ""}
                </span>
              </span>
              <span className="shrink-0 text-caption text-primary">추가</span>
            </button>
          </li>
        ))}
        </ul>
      </div>
    </div>
  );
}

/** 노드 종류별 작은 그림. 글자만 있는 목록은 훑기 어렵다(소유자 지적). */
function NodeGlyph({ kind }: { kind: GraphNode["kind"] }) {
  const path =
    kind === "tool" ? (
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    ) : kind === "llm" ? (
      <>
        <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
        <path d="M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" />
      </>
    ) : kind === "classify" ? (
      <>
        <path d="M4 12h5M13 6h7M13 12h7M13 18h7" />
        <path d="M9 12c2 0 2-6 4-6M9 12c2 0 2 6 4 6" />
      </>
    ) : kind === "extract" ? (
      <>
        <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
        <path d="M14 3v5h5M9 13h6M9 17h3" />
      </>
    ) : kind === "template" ? (
      <>
        <path d="M4 6h16M4 12h10M4 18h13" />
      </>
    ) : (
      <>
        <path d="M6 4v5a4 4 0 0 0 4 4h4" />
        <path d="M6 13v7M18 9l-4 4 4 4" />
      </>
    );
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {path}
    </svg>
  );
}
