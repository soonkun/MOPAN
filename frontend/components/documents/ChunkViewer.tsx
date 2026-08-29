import type { Chunk } from "@/lib/types";

export default function ChunkViewer({ chunks }: { chunks: Chunk[] }) {
  if (chunks.length === 0) {
    return <p className="text-sm text-gray-400">아직 청크가 없습니다.</p>;
  }
  return (
    <div className="space-y-3">
      {chunks.map((chunk) => (
        <div key={chunk.id} className="rounded border border-gray-200 p-3 text-sm">
          <div className="mb-1 flex flex-wrap gap-3 text-xs text-gray-400">
            <span>청크 {chunk.chunk_index}</span>
            {chunk.section && <span>소제목: {chunk.section}</span>}
            {chunk.page !== null && <span>{chunk.page}쪽</span>}
            <span>토큰 {chunk.token_count}개</span>
            <span>{chunk.char_count}자</span>
          </div>
          <p className="whitespace-pre-wrap text-gray-800">{chunk.content}</p>
        </div>
      ))}
    </div>
  );
}
