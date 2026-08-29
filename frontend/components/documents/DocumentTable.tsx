import Link from "next/link";
import type { DocumentItem, DocumentStatus } from "@/lib/types";

// Keyed on the DocumentStatus union, not `string`, so a new backend status is a
// compile error here rather than a raw enum value on screen. The `?? raw`
// fallback at the call site still covers a backend deployed ahead of this.
const STATUS_LABEL: Record<DocumentStatus, string> = {
  uploaded: "대기 중",
  parsing: "파싱 중",
  chunking: "청킹 중",
  embedding: "임베딩 중",
  indexed: "완료",
  failed: "실패",
};

// Raw enum values are not labels. `uppercase` on doc.file_type printed DOCX and
// MD at the user; the five values are the ALLOWED_EXTENSIONS set in
// backend/app/documents/validation.py. Exported because UploadDropzone derives
// both its `accept` attribute and its hint text from it: the same page used to
// offer "PDF, DOCX, TXT, MD, HTML" in the dropzone and render 웹문서/마크다운 in
// this column, two vocabularies for one set of formats, kept in step by hand.
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

// A frontend timeout would lie about a genuinely slow document, but elapsed time
// is a fact. `updated_at` is bumped by every _set_status commit in the pipeline,
// so this reads "no progress for N minutes", not "N minutes since upload" - which
// is what turns a job stuck at 대기 중 (no worker running, say) from a silent hang
// into something the user can act on.
function StalledNote({ doc }: { doc: DocumentItem }) {
  if (TERMINAL.has(doc.status)) return null;
  const minutes = Math.floor((Date.now() - Date.parse(doc.updated_at)) / 60000);
  if (minutes < 1) return null;
  return (
    <p className="mt-0.5 text-xs text-gray-500">
      {minutes}분째 {STATUS_LABEL[doc.status] ?? doc.status}
    </p>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function DocumentTable({ documents }: { documents: DocumentItem[] }) {
  if (documents.length === 0) {
    return <p className="py-8 text-center text-sm text-gray-400">문서가 없습니다.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-gray-500">
            <th className="py-2 pr-3">문서명</th>
            <th className="py-2 pr-3">분류</th>
            <th className="py-2 pr-3">형식</th>
            <th className="py-2 pr-3">등록자</th>
            <th className="py-2 pr-3">등록일</th>
            <th className="py-2 pr-3 text-right">청크 수</th>
            <th className="py-2 pr-3">상태</th>
            <th className="py-2 text-right">크기</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr key={doc.id} className="border-b border-gray-100 hover:bg-gray-50">
              <td className="py-2 pr-3">
                {/* Bounded, or the 문서명 column starves every column after it.
                    Measured at 1280x900 with a 244-character filename in the
                    corpus: unbounded, the table ran 2627px wide, this cell took
                    2261px and the 상태 cell was left 28px, rendering the failure
                    reason as a 15px-wide, 416px-tall ribbon of single characters.
                    With max-w-xs the same page is 976/333/193px and the reason
                    reads normally at 180x32px. Nothing escaped its cell either
                    way - the page stayed 1280px - so this is legibility, not
                    containment: the reason is unreadable exactly when a hostile
                    filename is present, which is the case it was made visible
                    for. `title` keeps the full name available on hover; the
                    untruncated text is still in the DOM for a screen reader. */}
                <Link
                  href={`/documents/${doc.id}`}
                  title={doc.filename}
                  className="block max-w-xs truncate hover:underline"
                >
                  {doc.filename}
                </Link>
              </td>
              <td className="py-2 pr-3 text-gray-500">{doc.collection_name ?? "-"}</td>
              <td className="py-2 pr-3 text-gray-500">
                {FILE_TYPE_LABEL[doc.file_type] ?? doc.file_type}
              </td>
              <td className="py-2 pr-3 text-gray-500">{doc.uploader_email ?? "-"}</td>
              <td className="py-2 pr-3 text-gray-500">
                {new Date(doc.created_at).toLocaleDateString()}
              </td>
              <td className="py-2 pr-3 text-right text-gray-500">{doc.chunk_count}</td>
              <td className="py-2 pr-3">
                <span className={doc.status === "failed" ? "text-red-600" : "text-gray-700"}>
                  {STATUS_LABEL[doc.status] ?? doc.status}
                </span>
                {/* Why a document failed only ever appears here: the upload POST
                    returned 202 long before the worker failed, so no banner on
                    the page ever sees this message. It was a `title` tooltip,
                    which is pointer-only - a keyboard or screen reader user got
                    "실패" and no reason at all. */}
                {doc.error_message && (
                  <p className="mt-0.5 max-w-xs text-xs text-red-600">{doc.error_message}</p>
                )}
                <StalledNote doc={doc} />
              </td>
              <td className="py-2 text-right text-gray-500">{formatSize(doc.size_bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
