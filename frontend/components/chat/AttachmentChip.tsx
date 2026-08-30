import { formatSize } from "@/components/documents/DocumentTable";

/** One attached file, in the composer and on a sent user turn alike. The two
 * differ only in what they pass: the composer passes a blob: preview and an
 * onRemove, a transcript passes /api/attachments/{id}/content and no remove. */
export default function AttachmentChip({
  filename,
  sizeBytes,
  kind,
  src,
  status,
  error,
  onRemove,
}: {
  filename: string;
  sizeBytes: number;
  kind: "image" | "document";
  src?: string | null;
  status?: "uploading" | "ready";
  error?: string | null;
  onRemove?: () => void;
}) {
  return (
    <div
      className={`flex max-w-[17rem] items-center gap-2 rounded-md px-2 py-1.5 ${
        error ? "bg-error-container text-on-error-container" : "bg-surface-container-high"
      }`}
    >
      {kind === "image" && src && !error ? (
        // The filename IS the alt text: "이미지" would tell a screen-reader user
        // nothing they could act on, and the filename is the only thing
        // distinguishing one attachment from the next in this row.
        <img
          src={src}
          alt={filename}
          className="h-10 w-10 shrink-0 rounded-xs object-cover"
        />
      ) : (
        <span
          aria-hidden="true"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xs bg-surface-container text-on-surface-variant"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
            <path d="M14 3v5h5" />
          </svg>
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-caption font-medium text-on-surface" title={filename}>
          {filename}
        </div>
        {/* The refusal renders HERE, on the attachment it belongs to, not as a
            page-level banner: with five chips in the row a banner cannot say
            which file it is about. A reason is the one line that must NOT be
            truncated - "지원하지 않는 파일 형식입…" tells the user nothing they
            can act on - so it wraps while a size stays on one line. */}
        <div
          className={
            error ? "break-keep text-caption" : "truncate text-caption text-on-surface-variant"
          }
        >
          {error ?? (status === "uploading" ? "업로드 중…" : formatSize(sizeBytes))}
        </div>
      </div>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`첨부 삭제: ${filename}`}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-on-surface-variant transition-colors duration-150 hover:bg-surface-container-highest"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      )}
    </div>
  );
}
