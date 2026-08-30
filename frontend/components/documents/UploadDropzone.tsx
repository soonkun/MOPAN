"use client";

import { useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import { FILE_TYPE_LABEL } from "@/components/documents/DocumentTable";
import type { DocumentItem } from "@/lib/types";

// One source of truth with the table's 형식 column, so the dropzone can never
// advertise a format the table has no label for, or vice versa.
const EXTENSIONS = Object.keys(FILE_TYPE_LABEL);
const ACCEPT = EXTENSIONS.map((ext) => `.${ext}`).join(",");
const FORMATS = Object.values(FILE_TYPE_LABEL).join(", ");

// Must match settings.max_upload_size_mb. backend/app/documents/validation.py is
// the real boundary; this only spares the user a 60MB upload that ends in a 413,
// and `accept` above cannot do it because it filters the picker, not a drop.
const MAX_UPLOAD_MB = 50;

function rejection(file: File): string | null {
  // Same rule as validation.py's extension_of: no dot means no extension, not
  // "the whole name is the extension".
  const extension = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!EXTENSIONS.includes(extension)) {
    return `지원하지 않는 파일 형식입니다: .${extension}`;
  }
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
    return `파일이 최대 크기 ${MAX_UPLOAD_MB}MB를 초과했습니다.`;
  }
  return null;
}

export default function UploadDropzone({
  collectionId,
  onUploaded,
}: {
  collectionId: string;
  onUploaded: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function uploadFile(file: File) {
    const refusal = rejection(file);
    if (refusal) {
      setError(refusal);
      return;
    }
    setError(null);
    setBusy(true);
    const formData = new FormData();
    formData.append("collection_id", collectionId);
    formData.append("file", file);
    try {
      // apiFetch handles FormData correctly: it only sets a JSON Content-Type for
      // string bodies, so the browser's multipart boundary survives.
      await apiFetch<DocumentItem>("/api/documents", { method: "POST", body: formData });
      onUploaded();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {/* A <button>, not a <div onClick>. As a div this was pointer-only: not in
          the tab order, no role, no Enter/Space handler, so the one control that
          gets a document into the system was unreachable without a mouse. The
          drag handlers sit on it just the same. */}
      <button
        type="button"
        disabled={busy}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          // `disabled` does NOT cover this. Measured on Edge/Chromium 152: a real
          // click on the disabled button produced 0 click events, but dragover and
          // drop dispatched at it both fired - so a second file dropped mid-upload
          // started a concurrent upload that the click path cannot start.
          if (busy) return;
          const file = e.dataTransfer.files[0];
          if (file) void uploadFile(file);
        }}
        onClick={() => inputRef.current?.click()}
        // The dashed outline is the exception §1 allows: it is not decorating a
        // box, it is drawing the drop target. Everything else here is tonal -
        // the container steps up to `high` while a file is over it.
        className={`w-full rounded-md border border-dashed p-8 text-center text-body transition-colors duration-150 ${
          dragging
            ? "border-primary bg-surface-container-high text-on-surface"
            : "border-outline bg-surface-container-low text-on-surface-variant"
        } ${busy ? "" : "cursor-pointer hover:bg-surface-container"}`}
      >
        {busy ? "업로드 중..." : `문서를 드래그하거나 클릭하여 업로드하세요 (${FORMATS})`}
      </button>
      {/* Outside the button, not inside it. Nesting a form control in a <button>
          violates the button content model, and the concrete symptom is
          re-entrancy, not a swallowed click: the input's own click bubbles back
          out through the button, whose onClick fires again. Measured on
          Edge/Chromium 152 with the same handler shape - nested, one real click
          gave 2 button-handler invocations and 1 input click; as siblings, 1 and
          1. The picker does open either way; it is the doubled handler that is
          the bug. */}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void uploadFile(file);
          e.target.value = "";
        }}
      />
      <ErrorBanner message={error} />
    </div>
  );
}
