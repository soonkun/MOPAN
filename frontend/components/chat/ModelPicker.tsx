"use client";

import PopoverSheet from "@/components/chat/PopoverSheet";
import type { AnswerModel } from "@/lib/types";

/** Which model answers the next question.
 *
 * No trigger of its own any more: the composer's + menu owns every entry point
 * into this list, so this component is the list and PopoverSheet is the box it
 * arrives in. `open` and `onClose` are what let the menu close itself before
 * this opens - two stacked sheets over a composer is one too many.
 */

export default function ModelPicker({
  models,
  value,
  onChange,
  open,
  onClose,
  anchorRef,
}: {
  models: AnswerModel[];
  value: string;
  onChange: (id: string) => void;
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
  // Selecting and dismissing are deliberately two different events, and this was
  // found by driving it: with `onChange` closing the sheet, the first ArrowDown
  // a keyboard user pressed moved the selection AND shut the menu, so they could
  // never reach the third model. `change` fires on an arrow key, `click` does not
  // - it fires on a pointer press and on Space, which are the two gestures that
  // MEAN "this one". So change commits the choice and click is what closes.
  // Escape closes too, and keeps whatever the arrows landed on, because the
  // choice was already committed on the way past.

  return (
    <PopoverSheet open={open} onClose={onClose} anchorRef={anchorRef} label="답변 모델">
      {/* Radios, not buttons: arrow-key navigation inside the group, the
          checked state announced, and one tab stop for the whole list all
          come from the platform. pb-6 is the phone's home indicator. */}
      <fieldset className="border-0 p-2 pb-6 sm:pb-2">
        <legend className="px-3 py-2 text-label font-medium text-on-surface-variant">
          답변 모델
        </legend>
        {models.map((model) => (
          <label
            key={model.id}
            className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-3 transition-colors duration-150 hover:bg-surface-container-high has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary sm:py-2"
          >
            <input
              type="radio"
              name="answer-model"
              value={model.id}
              checked={model.id === value}
              onChange={() => onChange(model.id)}
              // `detail` is the click count. A radio runs its full activation
              // behaviour for an ARROW key too - measured, the sheet closed on
              // the first ArrowDown - so `click` on its own cannot tell
              // browsing from choosing. A pointer press reports detail >= 1;
              // every keyboard-synthesised click reports 0.
              onClick={(event) => {
                if (event.detail > 0) onClose();
              }}
              // The keyboard half: Space and Enter mean "this one", arrows
              // mean "show me the next one".
              //
              // keyDOWN, not keyup, and this was measured too. A button is
              // activated by Enter on keydown, so opening the sheet with Enter
              // moved focus onto this radio in time for the SAME press's keyup
              // to land here and close it again - the sheet flickered open and
              // shut on one keystroke. A keydown belongs to whatever had focus
              // when the key went down, which is the distinction that fixes it.
              onKeyDown={(event) => {
                if (event.key === " " || event.key === "Enter") onClose();
              }}
              className="sr-only"
            />
            <span aria-hidden="true" className="h-4 w-4 shrink-0 text-primary">
              {model.id === value && (
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="m5 13 4 4L19 7" />
                </svg>
              )}
            </span>
            <span className="min-w-0 flex-1 truncate text-body">{model.label}</span>
            {model.is_default && <span className="shrink-0 text-caption text-on-surface-variant">기본</span>}
          </label>
        ))}
      </fieldset>
    </PopoverSheet>
  );
}
