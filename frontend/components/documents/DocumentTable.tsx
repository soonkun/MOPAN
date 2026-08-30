import Link from "next/link";
import type { DocumentItem, DocumentStatus } from "@/lib/types";

// Keyed on the DocumentStatus union, not `string`, so a new backend status is a
// compile error here rather than a raw enum value on screen. Exported because the
// page's 상태 filter builds its options from it.
export const STATUS_LABEL: Record<DocumentStatus, string> = {
  uploaded: "대기 중",
  parsing: "파싱 중",
  chunking: "청킹 중",
  embedding: "임베딩 중",
  indexed: "완료",
  failed: "실패",
};

// The five keys are the ALLOWED_EXTENSIONS set in
// backend/app/documents/validation.py. Exported because UploadDropzone derives
// its `accept` attribute, its hint text and its client-side precheck from it.
export const FILE_TYPE_LABEL: Record<string, string> = {
  pdf: "PDF",
  docx: "워드",
  txt: "텍스트",
  md: "마크다운",
  html: "웹문서",
};

// Exported so the page's poll gate and this file's stalled note agree on what
// "still working" means instead of keeping two copies of the same set.
export const TERMINAL = new Set<DocumentStatus>(["indexed", "failed"]);

// `updated_at` is bumped by every _set_status commit in the pipeline, so this
// reads "no progress for N minutes", not "N minutes since upload" - which is what
// turns a job stuck at 대기 중 (no worker running, say) into something the user
// can act on.
function StalledNote({ doc }: { doc: DocumentItem }) {
  if (TERMINAL.has(doc.status)) return null;
  const minutes = Math.floor((Date.now() - Date.parse(doc.updated_at)) / 60000);
  if (minutes < 1) return null;
  return (
    <p className="mt-0.5 text-caption text-on-surface-variant">
      {minutes}분째 {STATUS_LABEL[doc.status] ?? doc.status}
    </p>
  );
}

// Exported for the chat composer's attachment chips, which show the same fact in
// the same units. One formatter, so 1.5 MB is never 1536.0 KB two screens over.
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function DocumentTable({ documents }: { documents: DocumentItem[] }) {
  if (documents.length === 0) {
    return <p className="py-8 text-center text-body text-on-surface-variant">문서가 없습니다.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-sm">
      <table className="w-full text-left text-body">
        <thead>
          <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
            <th scope="col" className="px-3 py-3">문서명</th>
            <th scope="col" className="px-3 py-3">분류</th>
            <th scope="col" className="px-3 py-3">형식</th>
            <th scope="col" className="px-3 py-3">등록자</th>
            <th scope="col" className="px-3 py-3">등록일</th>
            <th scope="col" className="px-3 py-3 text-right">청크 수</th>
            {/* One 상태 column, not the spec's separate Embedding/Index pair.
                backend/app/rag/pipeline.py writes the vector and its row in one
                vector_store.upsert, and both retrieval indexes are Postgres-
                maintained on that insert, so no document can be embedded but not
                indexed. Two columns would always show the same value. */}
            <th scope="col" className="px-3 py-3">상태</th>
            <th scope="col" className="px-3 py-3 text-right">크기</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr key={doc.id} className="border-b border-outline-variant transition-colors duration-150 hover:bg-surface-container-low">
              <td className="px-3 py-3">
                {/* Bounded, or the 문서명 column starves every column after it.
                    Measured at 1280x900 with a 244-character filename: unbounded,
                    this cell took 2261px and left the 상태 cell 28px, rendering
                    the failure reason as a 15px-wide ribbon of single characters;
                    with max-w-xs the same reason reads normally at 180x32px. */}
                <Link
                  href={`/documents/${doc.id}`}
                  title={doc.filename}
                  className="block max-w-xs truncate hover:underline"
                >
                  {doc.filename}
                </Link>
              </td>
              <td className="px-3 py-3 text-on-surface-variant">{doc.collection_name ?? "-"}</td>
              <td className="px-3 py-3 text-on-surface-variant">
                {FILE_TYPE_LABEL[doc.file_type] ?? doc.file_type}
              </td>
              <td className="px-3 py-3 text-on-surface-variant">{doc.uploader_email ?? "-"}</td>
              <td className="px-3 py-3 text-on-surface-variant">
                {new Date(doc.created_at).toLocaleDateString()}
              </td>
              <td className="px-3 py-3 text-right text-on-surface-variant">{doc.chunk_count}</td>
              <td className="px-3 py-3">
                <span className={doc.status === "failed" ? "text-error" : "text-on-surface"}>
                  {STATUS_LABEL[doc.status] ?? doc.status}
                </span>
                {/* Why a document failed only ever appears here: the upload POST
                    returned 202 long before the worker failed, so no banner on
                    the page ever sees this message. */}
                {doc.error_message && (
                  <p className="mt-0.5 max-w-xs text-caption text-error">{doc.error_message}</p>
                )}
                <StalledNote doc={doc} />
              </td>
              <td className="px-3 py-3 text-right text-on-surface-variant">{formatSize(doc.size_bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
