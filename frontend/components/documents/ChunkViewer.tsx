import type { Chunk } from "@/lib/types";

export default function ChunkViewer({ chunks }: { chunks: Chunk[] }) {
  if (chunks.length === 0) {
    return <p className="text-body text-on-surface-variant">아직 청크가 없습니다.</p>;
  }
  return (
    <div className="space-y-3">
      {chunks.map((chunk) => (
        <div key={chunk.id} className="rounded-md bg-surface-container p-3 text-body">
          <div className="mb-1 flex flex-wrap gap-3 text-caption text-on-surface-variant">
            {/* chunk_index is 0-based on the wire; the heading is not. */}
            <span>청크 {chunk.chunk_index + 1}</span>
            <span className="break-all">ID {chunk.id}</span>
            {chunk.section && <span>소제목: {chunk.section}</span>}
            {chunk.page !== null && <span>{chunk.page}쪽</span>}
            <span>토큰 {chunk.token_count}개</span>
            <span>{chunk.char_count}자</span>
            <span>{chunk.embedded ? "임베딩 완료" : "임베딩 없음"}</span>
            {Object.keys(chunk.chunk_metadata).length > 0 && (
              <span className="break-all">
                메타데이터 {JSON.stringify(chunk.chunk_metadata)}
              </span>
            )}
          </div>
          <p className="whitespace-pre-wrap text-on-surface">{chunk.content}</p>
        </div>
      ))}
    </div>
  );
}
