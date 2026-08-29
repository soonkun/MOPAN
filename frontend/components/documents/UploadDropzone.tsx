"use client";

import { useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import { FILE_TYPE_LABEL } from "@/components/documents/DocumentTable";
import type { DocumentItem } from "@/lib/types";

// One source of truth with the table's 형식 column, so the dropzone can never
// advertise a format the table has no label for, or vice versa.
const ACCEPT = Object.keys(FILE_TYPE_LABEL)
  .map((ext) => `.${ext}`)
  .join(",");
const FORMATS = Object.values(FILE_TYPE_LABEL).join(", ");

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
          drag handlers sit on it just the same, and `disabled` while busy is
          what stops a second upload starting on top of the first. */}
      <button
        type="button"
        disabled={busy}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) void uploadFile(file);
        }}
        onClick={() => inputRef.current?.click()}
        className={`w-full rounded border-2 border-dashed p-8 text-center text-sm ${
          dragging ? "border-gray-500 bg-gray-50" : "border-gray-300"
        } ${busy ? "text-gray-400" : "cursor-pointer"}`}
      >
        {busy ? "업로드 중..." : `문서를 드래그하거나 클릭하여 업로드하세요 (${FORMATS})`}
      </button>
      {/* Outside the button: a form control nested in a button is invalid HTML
          and browsers swallow the click that should open the picker. */}
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
