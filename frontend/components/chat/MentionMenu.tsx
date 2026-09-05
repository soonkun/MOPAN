"use client";

import type { MentionEntry } from "@/lib/mention";

/** The list `@` opens in the composer.
 *
 * ONE list, because there is one Tool interface: `GET /api/tools` returns the
 * document search, every enabled MCP tool and every callable workflow in a
 * single namespace, and `ref` - `rag`, `mcp:서버/도구`, `workflow:이름` - is what
 * a graph node and a chip both write verbatim.
 *
 * NOT a <dialog>. Every other overlay in this composer is one, and this is the
 * exception on purpose: a modal takes focus, and the whole gesture here is that
 * the user keeps typing. The textarea stays focused and stays the thing being
 * driven - it becomes a combobox, the list is its popup, and the arrows and
 * Enter are forwarded to it. That is also what keeps the phone keyboard up: a
 * showModal() would drop it on the first `@`.
 *
 * The IME rule lives in Composer, not here, because it is a property of the
 * KEYSTROKE rather than of the list: see `updateMention` there. What a row IS,
 * and what the query matches, is in lib/mention.ts, where a test can reach it.
 */

const KIND_LABEL: Record<MentionEntry["kind"], string> = {
  rag: "문서 검색",
  mcp: "MCP 서버",
  workflow: "워크플로우",
};

const RISK_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};

export default function MentionMenu({
  id,
  entries,
  activeIndex,
  query,
  onPick,
  onHover,
}: {
  id: string;
  entries: MentionEntry[];
  activeIndex: number;
  query: string;
  onPick: (entry: MentionEntry) => void;
  onHover: (index: number) => void;
}) {
  return (
    <div className="p-1 pb-2">
      {/* max-h in vh, not px: at 390px with the keyboard up the visual viewport
          is about 340px tall, and a fixed 320px list would cover the composer
          that is producing it. */}
      <ul
        id={id}
        role="listbox"
        aria-label="부를 수 있는 것"
        className="max-h-[40vh] overflow-y-auto rounded-md bg-surface-container-high p-1 shadow-menu"
      >
        {entries.length === 0 ? (
          <li className="px-3 py-3 text-body text-on-surface-variant">
            {query.trim() ? `"${query.trim()}"에 맞는 것이 없습니다.` : "부를 수 있는 것이 없습니다."}
          </li>
        ) : (
          entries.map((entry, index) => (
            <li
              key={entry.key}
              id={`${id}-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              // onMouseDown, not onClick, and preventDefault with it: a click
              // moves focus out of the textarea first, which on a phone drops
              // the keyboard and on a desktop closes the menu before the pick
              // has been read.
              onMouseDown={(event) => {
                event.preventDefault();
                onPick(entry);
              }}
              onMouseEnter={() => onHover(index)}
              className={`cursor-pointer rounded-sm px-3 py-2 transition-colors duration-150 ${
                index === activeIndex
                  ? "bg-primary-container text-on-primary-container"
                  : "text-on-surface"
              }`}
            >
              <span className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-body font-medium">{entry.name}</span>
                <span
                  className={`shrink-0 text-caption ${
                    index === activeIndex ? "text-on-primary-container" : "text-on-surface-variant"
                  }`}
                >
                  {KIND_LABEL[entry.kind]}
                  {entry.riskLevel !== "read" && ` · ${RISK_LABEL[entry.riskLevel] ?? entry.riskLevel}`}
                </span>
              </span>
              <span
                className={`block truncate text-caption ${
                  index === activeIndex ? "text-on-primary-container" : "text-on-surface-variant"
                }`}
              >
                {entry.description || entry.ref}
              </span>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}
