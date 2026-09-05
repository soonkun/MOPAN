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
    <div className="pointer-events-auto absolute bottom-3 left-3 top-[9.75rem] z-10 flex w-60 max-w-[calc(100%-1.5rem)] flex-col rounded-md bg-surface-container shadow-menu">
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
      <p className="px-3 pb-2 text-caption text-on-surface-variant">
        누르면 화면 가운데에, 끌어다 놓으면 그 자리에 생깁니다.
      </p>

      <div className="flex gap-2 px-3 pb-3">
        {NODE_ITEMS.map((item) => (
          <button
            key={item.kind}
            type="button"
            draggable
            onClick={() => onAdd(item.kind)}
            onDragStart={(event) => {
              event.dataTransfer.setData("application/x-mopan-node", item.kind);
              event.dataTransfer.effectAllowed = "copy";
            }}
            title={item.hint}
            className="flex-1 rounded-sm bg-surface-container-high px-2 py-2 text-center text-label text-on-surface hover:bg-surface-container-highest"
          >
            + {item.label}
          </button>
        ))}
      </div>

      <div className="px-3 pb-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="부를 수 있는 것 찾기"
          className="field h-8 w-full text-caption"
          aria-label="부를 수 있는 것 찾기"
        />
      </div>

      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
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
              className="w-full rounded-sm px-2 py-1.5 text-left hover:bg-surface-container-high"
            >
              <span className="block truncate text-body text-on-surface">{callable.name}</span>
              <span className="block truncate text-caption text-on-surface-variant">
                {kindOf(callable)}
                {callable.description ? ` · ${callable.description}` : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
