"use client";

import { useRef, useState } from "react";
import type { AgentOption } from "@/lib/types";

/** Which AGENT answers the next question - a saved configuration of prompt,
 * corpus scope, tool list, model and orchestrator.
 *
 * The same native <dialog> ModelPicker.tsx is, for the same reasons, and
 * deliberately not a shared abstraction of it: the two differ in their list
 * (this one carries a null "기본" row and a description line), and an
 * options-and-slots component covering both would be longer than the second
 * copy. If a third picker appears, that is the moment to extract one.
 *
 * The one behaviour worth reading twice is the same one ModelPicker documents
 * at length: `change` commits the choice so arrow keys can browse, and `click`
 * with `detail > 0` - a real pointer press - is what closes. */

// Must equal `sm:w-72` below; the anchoring maths needs the number. Wider than
// the model picker's 240 because a row here carries a description line.
const MENU_WIDTH = 288;
const EDGE = 8;

// The default agent has no row in the database, so it has no id. `null` is that
// agent everywhere in this client, and it is what makes "no agents configured"
// and "the 기본 row is selected" the same state rather than two.
export const DEFAULT_AGENT_LABEL = "기본";

export default function AgentPicker({
  agents,
  value,
  onChange,
}: {
  agents: AgentOption[];
  value: string | null;
  onChange: (id: string | null) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  const current = agents.find((a) => a.id === value) ?? null;

  function openPicker() {
    const dialog = dialogRef.current;
    const trigger = triggerRef.current;
    if (!dialog || !trigger) return;
    if (window.matchMedia("(min-width: 640px)").matches) {
      const rect = trigger.getBoundingClientRect();
      const left = Math.min(
        Math.max(EDGE, rect.right - MENU_WIDTH),
        window.innerWidth - MENU_WIDTH - EDGE,
      );
      dialog.style.left = `${left}px`;
      dialog.style.right = "auto";
      dialog.style.top = "auto";
      dialog.style.bottom = `${window.innerHeight - rect.top + EDGE}px`;
    } else {
      dialog.style.cssText = "";
    }
    dialog.showModal();
    setOpen(true);
  }

  // Nothing to choose between when no agent is configured: the 기본 row alone is
  // not a choice, and an empty agents table has to leave the composer exactly as
  // it was before this control existed.
  if (agents.length === 0) return null;

  const rows: (AgentOption | null)[] = [null, ...agents];

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        // A pointer press that moves focus off the textarea dismisses the phone
        // keyboard under the user; the same rule every control in this row keeps.
        onMouseDown={(event) => event.preventDefault()}
        onClick={openPicker}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`에이전트: ${current?.name ?? DEFAULT_AGENT_LABEL}`}
        className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-2 text-label transition-colors duration-150 sm:px-3 ${
          current
            ? "bg-primary-container text-on-primary-container"
            : "text-on-surface-variant hover:bg-surface-container-high"
        }`}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-4 w-4 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="M12 3 4 7v5c0 4.4 3.2 8.2 8 9 4.8-.8 8-4.6 8-9V7l-8-4Z" />
          <path d="m9 12 2 2 4-4" />
        </svg>
        <span aria-hidden="true" className="hidden max-w-[8rem] truncate sm:inline">
          {current?.name ?? DEFAULT_AGENT_LABEL}
        </span>
      </button>

      <dialog
        ref={dialogRef}
        aria-labelledby="agent-picker-title"
        onClose={() => {
          setOpen(false);
          triggerRef.current?.focus();
        }}
        onClick={(event) => {
          if (event.target === dialogRef.current) dialogRef.current.close();
        }}
        className="fixed inset-x-0 bottom-0 top-auto m-0 w-full max-w-none rounded-t-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim sm:w-72 sm:rounded-md sm:shadow-menu sm:backdrop:bg-transparent"
      >
        <fieldset className="border-0 p-2 pb-6 sm:pb-2">
          <legend
            id="agent-picker-title"
            className="px-3 py-2 text-label font-medium text-on-surface-variant"
          >
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
                  if (event.detail > 0) dialogRef.current?.close();
                }}
                onKeyDown={(event) => {
                  if (event.key === " " || event.key === "Enter") dialogRef.current?.close();
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
      </dialog>
    </>
  );
}
