"use client";

import PopoverSheet from "@/components/chat/PopoverSheet";
import type { AgentOption } from "@/lib/types";

/** Which AGENT answers the next question - a saved configuration of prompt,
 * corpus scope, tool list, model and orchestrator.
 *
 * The same controlled PopoverSheet ModelPicker.tsx is, and deliberately still
 * not a shared abstraction of its LIST: the two differ in what a row carries
 * (this one has a null "기본" row and a description line), and an
 * options-and-slots component covering both would be longer than the second
 * copy. What they did share - the anchored dialog, Escape, focus return - is
 * PopoverSheet, and that is now written once.
 *
 * The one behaviour worth reading twice is the same one ModelPicker documents
 * at length: `change` commits the choice so arrow keys can browse, and `click`
 * with `detail > 0` - a real pointer press - is what closes. */

// The default agent has no row in the database, so it has no id. `null` is that
// agent everywhere in this client, and it is what makes "no agents configured"
// and "the 기본 row is selected" the same state rather than two.
export const DEFAULT_AGENT_LABEL = "기본";

export default function AgentPicker({
  agents,
  value,
  onChange,
  open,
  onClose,
  anchorRef,
}: {
  agents: AgentOption[];
  value: string | null;
  onChange: (id: string | null) => void;
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
  const rows: (AgentOption | null)[] = [null, ...agents];

  return (
    <PopoverSheet open={open} onClose={onClose} anchorRef={anchorRef} label="에이전트">
      <fieldset className="border-0 p-2 pb-6 sm:pb-2">
        <legend className="px-3 py-2 text-label font-medium text-on-surface-variant">
          에이전트
        </legend>
        {rows.map((agent) => (
          <label
            key={agent?.id ?? "default"}
            className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-3 transition-colors duration-150 hover:bg-surface-container-high has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary sm:py-2"
          >
            <input
              type="radio"
              name="chat-agent"
              value={agent?.id ?? ""}
              checked={(agent?.id ?? null) === value}
              onChange={() => onChange(agent?.id ?? null)}
              onClick={(event) => {
                if (event.detail > 0) onClose();
              }}
              onKeyDown={(event) => {
                if (event.key === " " || event.key === "Enter") onClose();
              }}
              className="sr-only"
            />
            <span aria-hidden="true" className="h-4 w-4 shrink-0 text-primary">
              {(agent?.id ?? null) === value && (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="m5 13 4 4L19 7" />
                </svg>
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-body">
                {agent?.name ?? DEFAULT_AGENT_LABEL}
              </span>
              <span className="block truncate text-caption text-on-surface-variant">
                {agent
                  ? agent.description || "설명 없음"
                  : "이 배포의 기본 설정으로 답변합니다."}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
    </PopoverSheet>
  );
}
