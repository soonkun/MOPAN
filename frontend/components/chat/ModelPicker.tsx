"use client";

import { useRef, useState } from "react";
import type { AnswerModel } from "@/lib/types";

/** Which model answers the next question.
 *
 * ONE native <dialog>, two placements. showModal() is what buys the focus trap,
 * Escape, an inert background and top-layer stacking - the same reasoning
 * ConfirmDialog.tsx gives - and none of it has to be written here. The
 * difference between the desktop menu and the mobile sheet is where the box
 * sits, which is CSS plus four inline properties, not a second component.
 *
 * The composer is pinned to the bottom of the viewport, so a menu that opened
 * DOWNWARD from its trigger would open off-screen. On desktop it is anchored
 * above the trigger; on a phone it is a bottom sheet, which is what the owner
 * asked for and what every phone keyboard-adjacent menu does, for the same
 * reason: the bottom of the screen is where the thumb is. */

// Must equal the `sm:w-60` below - the anchoring maths needs the number.
const MENU_WIDTH = 240;
const EDGE = 8;

export default function ModelPicker({
  models,
  value,
  onChange,
}: {
  models: AnswerModel[];
  value: string;
  onChange: (id: string) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Mirrors the dialog's open state purely so aria-expanded can be announced;
  // the <dialog> itself is the source of truth and `onClose` is what syncs it,
  // so Escape - which fires no click handler - cannot leave the two disagreeing.
  const [open, setOpen] = useState(false);

  const current = models.find((m) => m.id === value) ?? models[0];

  function openPicker() {
    const dialog = dialogRef.current;
    const trigger = triggerRef.current;
    if (!dialog || !trigger) return;
    if (window.matchMedia("(min-width: 640px)").matches) {
      const rect = trigger.getBoundingClientRect();
      // Right-aligned to the trigger and clamped to the viewport, so the menu
      // cannot hang off either edge on a narrow desktop window.
      const left = Math.min(
        Math.max(EDGE, rect.right - MENU_WIDTH),
        window.innerWidth - MENU_WIDTH - EDGE,
      );
      dialog.style.left = `${left}px`;
      dialog.style.right = "auto";
      dialog.style.top = "auto";
      dialog.style.bottom = `${window.innerHeight - rect.top + EDGE}px`;
    } else {
      // Back to the class-driven bottom sheet. Without this a resize from
      // desktop to phone width would keep the anchored coordinates.
      dialog.style.cssText = "";
    }
    dialog.showModal();
    setOpen(true);
  }

  // Selecting and dismissing are deliberately two different events, and this was
  // found by driving it: with `onChange` closing the sheet, the first ArrowDown
  // a keyboard user pressed moved the selection AND shut the menu, so they could
  // never reach the third model. `change` fires on an arrow key, `click` does not
  // - it fires on a pointer press and on Space, which are the two gestures that
  // MEAN "this one". So change commits the choice and click is what closes.
  // Escape closes too, and keeps whatever the arrows landed on, because the
  // choice was already committed on the way past.

  if (models.length < 2) return null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        // Same reason as the composer's + button: reaching for the model picker
        // is the user still composing, and a pointer press that moves focus off
        // the textarea dismisses the phone keyboard under them.
        onMouseDown={(event) => event.preventDefault()}
        onClick={openPicker}
        aria-haspopup="dialog"
        aria-expanded={open}
        // The name carries the CURRENT selection, because that is the question a
        // screen reader user has when they land on this control. The visible
        // label is hidden from AT so it is not read twice.
        aria-label={`답변 모델: ${current.label}`}
        className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-2 text-label text-on-surface-variant transition-colors duration-150 hover:bg-surface-container-high sm:px-3"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-4 w-4 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <rect x="8" y="8" width="8" height="8" rx="1.5" />
          <path d="M10 4v3M14 4v3M10 17v3M14 17v3M4 10h3M4 14h3M17 10h3M17 14h3" />
        </svg>
        {/* The label costs ~70px of a 390px composer, where the + button, the
            textarea and 전송 all have to fit; the icon and the accessible name
            carry it there instead. */}
        <span aria-hidden="true" className="hidden max-w-[8rem] truncate sm:inline">
          {current.label}
        </span>
      </button>

      <dialog
        ref={dialogRef}
        aria-labelledby="model-picker-title"
        onClose={() => {
          setOpen(false);
          // Explicit, not left to the UA: focus has to land back on the control
          // the user opened, or a keyboard user is returned to the top of the
          // document with the composer behind them.
          triggerRef.current?.focus();
        }}
        // A transparent desktop backdrop still fills the viewport, so this is
        // what closes the menu on an outside click. `=== dialog` because every
        // click inside a child bubbles to the dialog too.
        onClick={(event) => {
          if (event.target === dialogRef.current) dialogRef.current.close();
        }}
        // Mobile: a bottom sheet pinned to the bottom edge, full width, rounded
        // on top only. Desktop: 240px anchored above the trigger by openPicker,
        // and no scrim - it is a menu, not a modal, whatever showModal() calls it.
        className="fixed inset-x-0 bottom-0 top-auto m-0 w-full max-w-none rounded-t-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim sm:w-60 sm:rounded-md sm:shadow-menu sm:backdrop:bg-transparent"
      >
        {/* Radios, not buttons: arrow-key navigation inside the group, the
            checked state announced, and one tab stop for the whole list all
            come from the platform. pb-6 is the phone's home indicator. */}
        <fieldset className="border-0 p-2 pb-6 sm:pb-2">
          <legend id="model-picker-title" className="px-3 py-2 text-label font-medium text-on-surface-variant">
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
                  if (event.detail > 0) dialogRef.current?.close();
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
                  if (event.key === " " || event.key === "Enter") dialogRef.current?.close();
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
      </dialog>
    </>
  );
}
