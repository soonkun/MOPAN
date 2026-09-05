"use client";

import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";

/** Confirmation for a destructive action. Native <dialog> + showModal(), the
 * same pattern as CitationBadge: focus trap, Escape, an inert background and
 * top-layer stacking all come with it, and none of them have to be written
 * here. Mounted only while a target is chosen, so mount means open.
 *
 * It runs `onConfirm` itself rather than handing the click back and closing,
 * because the 409 these screens exist to surface - "문서 N개가 들어 있는
 * 분류는..." - arrives AFTER the click. Closing first would put that message on
 * a page the user is no longer looking at; instead the dialog stays open and
 * renders it under the button that was pressed, and closes only on success. */
export default function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      dialogRef.current?.close();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="confirm-title"
      // Escape closes a <dialog> natively without firing any click handler, so
      // this is what keeps the parent's `target` state in step with the DOM.
      onClose={onClose}
      // §4: a dialog is one of exactly two things in this app allowed a
      // box-shadow, because it genuinely floats above the page.
      className="motion-pop w-full max-w-md rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      <div className="p-6">
        <h2 id="confirm-title" className="text-title font-medium">
          {title}
        </h2>
        <p className="mt-3 text-body text-on-surface-variant">{message}</p>
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" onClick={() => dialogRef.current?.close()} className="btn-text">
            취소
          </button>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={busy}
            className="btn-danger"
          >
            {busy ? "처리 중..." : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
