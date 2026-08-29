import CitationBadge from "@/components/chat/CitationBadge";
import type { Citation, Message } from "@/lib/types";

const MARKER = /\[(\d{1,2})\]/g;

/** Replaces inline [n] markers with clickable badges, so clicking a citation IN
 *  the answer opens its source - rather than showing a literal "[1]" next to an
 *  unrelated badge row at the bottom. */
function renderContent(content: string, citations: Citation[]): React.ReactNode[] {
  const byIndex = new Map(citations.map((c) => [c.index, c]));
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  MARKER.lastIndex = 0;

  while ((match = MARKER.exec(content)) !== null) {
    const citation = byIndex.get(Number(match[1]));
    if (!citation) continue;
    if (match.index > cursor) nodes.push(content.slice(cursor, match.index));
    nodes.push(<CitationBadge key={`${match.index}-${citation.index}`} citation={citation} />);
    cursor = match.index + match[0].length;
  }
  if (cursor < content.length) nodes.push(content.slice(cursor));
  return nodes;
}

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-2xl whitespace-pre-wrap rounded px-4 py-2 text-sm ${
          isUser ? "bg-gray-900 text-white" : "border border-gray-200 bg-gray-50 text-gray-900"
        }`}
      >
        {isUser ? message.content : renderContent(message.content, message.citations)}
        {!isUser && message.citations.length > 0 && (
          <div className="mt-2 border-t border-gray-200 pt-2 text-xs text-gray-500">
            {message.citations.map((c) => (
              // index, not chunk_id: chunk_id is null for an MCP citation, and
              // two of them on one message would collide on a null key. index
              // is unique per message by construction - the backend assigns it
              // with enumerate(used, start=1).
              <div key={c.index} className="truncate">
                [{c.index}] {c.filename ?? "출처"}
                {c.page !== null ? `, ${c.page}쪽` : ""}
                {c.section ? `, ${c.section}` : ""}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
