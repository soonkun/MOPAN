import Link from "next/link";
import DataTable from "@/components/ui/DataTable";
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
  xlsx: "엑셀",
  csv: "CSV",
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

export type SortKey = "name" | "created_at" | "chunks" | "status" | "size";

export default function DocumentTable({
  documents,
  onDownload,
  onDelete,
  selected,
  onToggle,
  onToggleAll,
  sort,
  order,
  onSort,
  showFolder = false,
  draggable = false,
}: {
  documents: DocumentItem[];
  onDownload: (doc: DocumentItem) => void;
  onDelete?: (doc: DocumentItem) => void;
  /** 체크박스 선택(탐색기의 일괄 작업). 셋 다 있을 때만 열이 그려진다. */
  selected?: Set<string>;
  onToggle?: (id: string) => void;
  onToggleAll?: (checked: boolean) => void;
  /** 서버 정렬. onSort가 있으면 해당 머리글이 버튼이 된다. */
  sort?: SortKey;
  order?: "asc" | "desc";
  onSort?: (key: SortKey) => void;
  /** 하위 폴더 포함 보기에서 문서가 어느 폴더에 있는지 보인다. */
  showFolder?: boolean;
  /** 행을 끌어 폴더 트리에 놓을 수 있게. dataTransfer에 문서 id. */
  draggable?: boolean;
}) {
  const selectable = selected !== undefined && onToggle !== undefined && onToggleAll !== undefined;
  const allChecked = selectable && documents.length > 0 && documents.every((d) => selected.has(d.id));
  function header(label: string, key: SortKey, className = "") {
    if (!onSort) return <th scope="col" className={`px-3 py-3 ${className}`}>{label}</th>;
    const active = sort === key;
    return (
      <th scope="col" className={`px-3 py-3 ${className}`} aria-sort={active ? (order === "asc" ? "ascending" : "descending") : "none"}>
        <button type="button" onClick={() => onSort(key)} className={`inline-flex items-center gap-1 hover:text-on-surface ${active ? "text-on-surface" : ""}`}>
          {label}
          <span aria-hidden="true" className="text-caption">{active ? (order === "asc" ? "▲" : "▼") : "↕"}</span>
        </button>
      </th>
    );
  }
  if (documents.length === 0) {
    return <p className="py-8 text-center text-body text-on-surface-variant">문서가 없습니다.</p>;
  }
  return (
    <DataTable caption="등록된 문서 목록">
        <thead>
          <tr className="whitespace-nowrap bg-surface-container-low text-label font-medium text-on-surface-variant">
            {selectable && (
              <th scope="col" className="w-8 px-3 py-3">
                <input type="checkbox" checked={allChecked} onChange={(e) => onToggleAll(e.target.checked)} aria-label="전체 선택" />
              </th>
            )}
            {header("문서명", "name")}
            {showFolder && <th scope="col" className="px-3 py-3">폴더</th>}
            <th scope="col" className="px-3 py-3">분류</th>
            <th scope="col" className="px-3 py-3">형식</th>
            <th scope="col" className="px-3 py-3">등록자</th>
            {header("등록일", "created_at")}
            {header("청크 수", "chunks", "text-right")}
            {/* One 상태 column, not the spec's separate Embedding/Index pair.
                backend/app/rag/pipeline.py writes the vector and its row in one
                vector_store.upsert, and both retrieval indexes are Postgres-
                maintained on that insert, so no document can be embedded but not
                indexed. Two columns would always show the same value. */}
            {header("상태", "status")}
            {header("크기", "size", "text-right")}
            <th scope="col" className="px-3 py-3">작업</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr
              key={doc.id}
              draggable={draggable}
              onDragStart={draggable ? (e) => { e.dataTransfer.setData("text/mopan-document", doc.id); e.dataTransfer.effectAllowed = "move"; } : undefined}
              className={`border-b border-outline-variant transition-colors duration-150 hover:bg-surface-container-low ${
                selected?.has(doc.id) ? "bg-primary-container/30" : ""
              } ${draggable ? "cursor-grab" : ""}`}
            >
              {selectable && (
                <td className="px-3 py-3">
                  <input type="checkbox" checked={selected.has(doc.id)} onChange={() => onToggle(doc.id)} aria-label={`${doc.filename} 선택`} />
                </td>
              )}
              <td className="px-3 py-3">
                {/* Bounded, or the 문서명 column starves every column after it.
                    Measured at 1280x900 with a 244-character filename: unbounded,
                    this cell took 2261px and left the 상태 cell 28px, rendering
                    the failure reason as a 15px-wide ribbon of single characters;
                    with max-w-xs the same reason reads normally at 180x32px. */}
                <Link
                  href={`/documents/${doc.id}`}
                  title={doc.filename}
                  className="line-clamp-2 block min-w-[9rem] max-w-xs break-all hover:underline"
                >
                  {doc.filename}
                </Link>
                {doc.stale && (
                  <span className="mt-0.5 mr-1 inline-block rounded-full bg-error-container/60 px-1.5 text-caption text-on-error-container" title="시행일(또는 등록일)이 기준 일수를 넘은 현행 문서 - 현행화 여부를 확인하세요">
                    점검 필요
                  </span>
                )}
                {(doc.version > 1 || !doc.is_current) && (
                  <span className={`mt-0.5 inline-block rounded-full px-1.5 text-caption ${doc.is_current ? "bg-primary-container text-on-primary-container" : "bg-surface-container-high text-on-surface-variant line-through"}`}>
                    v{doc.version}{doc.is_current ? "" : " 교체됨"}
                  </span>
                )}
              </td>
              {showFolder && <td className="px-3 py-3 text-on-surface-variant"><span className="line-clamp-2 max-w-[10rem]" title={doc.folder_path ?? undefined}>{doc.folder_path ?? "(루트)"}</span></td>}
              <td className="px-3 py-3 text-on-surface-variant"><span className="line-clamp-2 max-w-[8rem]">{doc.collection_name ?? "-"}</span></td>
              <td className="px-3 py-3 text-on-surface-variant">
                {FILE_TYPE_LABEL[doc.file_type] ?? doc.file_type}
              </td>
              <td className="px-3 py-3 text-on-surface-variant"><span className="line-clamp-2 max-w-[10rem] break-all" title={doc.uploader_email ?? undefined}>{doc.uploader_email ?? "-"}</span></td>
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
              <td className="px-3 py-3">
                {/* A button, not an <a href download>: Chrome saves whatever
                    body a same-origin response carries, so a document whose
                    stored file has gone missing would download its own 404 as a
                    file full of JSON. onDownload routes the Korean detail to
                    the page's error banner instead. The accessible name carries
                    the filename - every row's control would otherwise be named
                    다운로드, which names nothing in a list. */}
                {/* flex-wrap, and it is load-bearing. Two buttons side by side
                    add ~55px to this column's minimum width, which at 1280 -
                    the width this app is actually used at - pushed the table
                    46px past its scroll box and put 삭제 off-screen behind a
                    horizontal scrollbar. Wrapping drops the minimum to one
                    button, so the column stacks them at 1280 and keeps them on
                    one line from 1440 up. Measured: 46px of table overflow
                    before, 0 after, at 1280/1440/1920. */}
                <div className="flex flex-wrap items-center gap-1">
                  <button
                    type="button"
                    onClick={() => onDownload(doc)}
                    aria-label={`${doc.filename} 원본 파일 다운로드`}
                    className="btn-text btn-compact"
                  >
                    받기
                  </button>
                  {/* The accessible name carries the filename for the same
                      reason 받기 does: every row's delete control would
                      otherwise be named 삭제, which names nothing in a list. */}
                  {onDelete && (
                    <button
                      type="button"
                      onClick={() => onDelete(doc)}
                      aria-label={`${doc.filename} 삭제`}
                      className="btn-danger btn-compact"
                    >
                      삭제
                    </button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
    </DataTable>
  );
}
