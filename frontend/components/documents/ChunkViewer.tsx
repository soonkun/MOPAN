import type { Chunk } from "@/lib/types";

// This list exists to check that chunking came out sensibly, not to read the
// document - so a row is one line, and everything else waits behind a click.
// The corpus already holds a document with 1900+ chunks and the old row printed
// id, 소제목, 쪽, 토큰, 자, 임베딩 상태 and the metadata JSON above every chunk's
// full text, which is what made that list unreadable.
const PREVIEW_CHARS = 120;
// Slice before the collapse, not after: on 1900 chunks the regex would otherwise
// walk about 2MB of text on every render to produce 120 characters.
const SLICE_CHARS = PREVIEW_CHARS * 2;

/** One line, ending in … when there is more. The … is written in rather than
 * left to text-overflow so it is there at any column width - `truncate` still
 * clips on top of it on a narrow screen. */
function preview(content: string): string {
  const head = content.slice(0, SLICE_CHARS).replace(/\s+/g, " ").trim();
  if (head.length <= PREVIEW_CHARS && content.length <= SLICE_CHARS) return head;
  return `${head.slice(0, PREVIEW_CHARS).trimEnd()}…`;
}

/** <details>/<summary>, not a useState toggle: it is focusable, it opens on
 * Enter and Space, and it announces its own expanded state - all of which would
 * be a role, a tabIndex, an aria-expanded and two key handlers written by hand.
 * The marker is hidden (Safari needs the -webkit- pseudo as well as list-none)
 * and replaced with a chevron that rotates on open, because the UA triangle is
 * not on this app's type scale. */
export default function ChunkViewer({ chunks }: { chunks: Chunk[] }) {
  if (chunks.length === 0) {
    return <p className="text-body text-on-surface-variant">아직 청크가 없습니다.</p>;
  }
  return (
    <div className="space-y-1.5">
      {chunks.map((chunk) => (
        <details key={chunk.id} className="group rounded-md bg-surface-container">
          <summary className="flex cursor-pointer list-none items-center gap-3 rounded-md px-3 py-2 transition-colors duration-150 hover:bg-surface-container-high [&::-webkit-details-marker]:hidden">
            <span
              aria-hidden="true"
              className="shrink-0 text-caption text-on-surface-variant transition-transform duration-150 group-open:rotate-90"
            >
              ▶
            </span>
            {/* chunk_index is 0-based on the wire; the label is not. This is the
                only thing besides the text that identifies a collapsed row. */}
            <span className="shrink-0 text-caption text-on-surface-variant">
              청크 {chunk.chunk_index + 1}
            </span>
            <span className="min-w-0 flex-1 truncate text-body text-on-surface">
              {preview(chunk.content)}
            </span>
          </summary>
          <div className="space-y-2 px-3 pb-3 pt-1">
            <div className="flex flex-wrap gap-3 text-caption text-on-surface-variant">
              <span className="break-all">ID {chunk.id}</span>
              {chunk.section && <span>소제목: {chunk.section}</span>}
              {chunk.page !== null && <span>{chunk.page}쪽</span>}
              <span>토큰 {chunk.token_count}개</span>
              <span>{chunk.char_count}자</span>
              <span>{chunk.embedded ? "임베딩 완료" : "임베딩 없음"}</span>
              {Object.keys(chunk.chunk_metadata).length > 0 && (
                <span className="break-all">메타데이터 {JSON.stringify(chunk.chunk_metadata)}</span>
              )}
            </div>
            <p className="whitespace-pre-wrap text-body text-on-surface">{chunk.content}</p>
          </div>
        </details>
      ))}
    </div>
  );
}
