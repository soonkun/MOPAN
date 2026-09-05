"use client";

import { useEffect, useRef } from "react";

/** The anchored menu that is a bottom sheet on a phone.
 *
 * ModelPicker and AgentPicker each wrote this, and the composer's + menu would
 * have been the third copy - which is the moment those files' own comment named
 * for extracting it ("If a third picker appears, that is the moment to extract
 * one"). Everything that was identical lives here; what differs is the list,
 * which is the children.
 *
 * ONE native <dialog>, two placements. showModal() is what buys the focus trap,
 * Escape, an inert background and top-layer stacking - the same reasoning
 * ConfirmDialog.tsx gives - and none of it has to be written here. The composer
 * is pinned to the bottom of the viewport, so a menu that opened DOWNWARD from
 * its anchor would open off-screen; on desktop it is anchored above, and on a
 * phone it is a bottom sheet, which is where the thumb is.
 *
 * CONTROLLED, unlike the two pickers it replaces. The composer's + menu hands
 * off to a picker - close the menu, open the model list - and that hand-off is
 * one state change in one owner rather than two components reaching into each
 * other. It is also why `closingRef` exists: a close driven by the `open` prop
 * going false is the owner already knowing, and firing `onClose` back at it
 * would immediately cancel the sheet it was opening instead. Only a dismissal
 * the USER caused - Escape, the backdrop - is reported.
 *
 * Focus return is deliberately NOT here. There is exactly one owner of it, the
 * composer's `closeSheet`, because the hand-off case has to skip it: a sheet
 * that focused the anchor on its way out would pull focus straight back off the
 * picker it just opened. Two places doing it is how that bug returns.
 */

// Must equal `sm:w-72` below; the anchoring maths needs the number.
const MENU_WIDTH = 288;
const EDGE = 8;

export default function PopoverSheet({
  open,
  onClose,
  anchorRef,
  label,
  children,
}: {
  open: boolean;
  /** A dismissal the user caused. A close the owner asked for by flipping
   * `open` does not call this - see closingRef. */
  onClose: () => void;
  /** What the sheet hangs off on desktop. */
  anchorRef: React.RefObject<HTMLElement | null>;
  label: string;
  children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closingRef = useRef(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!open) {
      if (dialog.open) {
        closingRef.current = true;
        dialog.close();
      }
      return;
    }
    if (dialog.open) return;
    const anchor = anchorRef.current;
    if (anchor && window.matchMedia("(min-width: 640px)").matches) {
      const rect = anchor.getBoundingClientRect();
      // Left-aligned to the anchor and clamped to the viewport, so the menu
      // cannot hang off either edge on a narrow desktop window. Left, not
      // right: the anchor is the composer's + button, which is the LEFTMOST
      // control in the row, and a right-aligned box would open across it.
      const left = Math.min(
        Math.max(EDGE, rect.left),
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
  }, [open, anchorRef]);

  return (
    <dialog
      ref={dialogRef}
      aria-label={label}
      onClose={() => {
        if (closingRef.current) {
          closingRef.current = false;
          return;
        }
        onClose();
      }}
      // A transparent desktop backdrop still fills the viewport, so this is
      // what closes the menu on an outside click. `=== dialog` because every
      // click inside a child bubbles to the dialog too.
      onClick={(event) => {
        if (event.target === dialogRef.current) dialogRef.current.close();
      }}
      // Mobile: a bottom sheet pinned to the bottom edge, full width, rounded
      // on top only. Desktop: 288px anchored above the trigger by the effect,
      // and no scrim - it is a menu, not a modal, whatever showModal() calls it.
      className="motion-pop motion-sheet fixed inset-x-0 bottom-0 top-auto m-0 w-full max-w-none rounded-t-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim sm:w-72 sm:rounded-md sm:shadow-menu sm:backdrop:bg-transparent"
    >
      {children}
    </dialog>
  );
}
