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
      className="w-full max-w-md rounded border border-gray-200 bg-white p-0 text-gray-900 backdrop:bg-black/30"
    >
      <div className="p-4">
        <h2 id="confirm-title" className="text-sm font-semibold">
          {title}
        </h2>
        <p className="mt-2 text-sm text-gray-700">{message}</p>
        <div className="mt-2">
          <ErrorBanner message={error} />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-100"
          >
            취소
          </button>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={busy}
            className="rounded border border-red-300 bg-red-50 px-3 py-1.5 text-sm text-red-700 hover:bg-red-100 disabled:opacity-50"
          >
            {busy ? "처리 중..." : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
