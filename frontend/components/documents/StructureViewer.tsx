import type { Block } from "@/lib/types";

// `uppercase` on block.block_type showed the user HEADING / PARAGRAPH /
// LIST_ITEM / TABLE_CELL. The four keys are Block["block_type"] in lib/types.ts,
// so a new parser block type is a compile error here rather than English on
// screen.
const BLOCK_LABEL: Record<Block["block_type"], string> = {
  heading: "제목",
  paragraph: "본문",
  list_item: "목록",
  table_cell: "표",
};

/** Left pane of the detail view: the parsed original structure, so chunking
 *  quality can be judged against what the parser actually saw. */
export default function StructureViewer({ blocks }: { blocks: Block[] }) {
  if (blocks.length === 0) {
    return <p className="text-body text-on-surface-variant">원문 구조를 불러올 수 없습니다.</p>;
  }
  return (
    <div className="space-y-2">
      {blocks.map((block, index) => (
        <div key={index} className="text-body">
          <span className="mr-2 text-caption text-on-surface-variant">
            {/* The Record type makes a new block type a compile error, but the
                wire type is `str` - a backend deployed ahead of the frontend
                would otherwise render an empty label with no clue why. */}
            {BLOCK_LABEL[block.block_type] ?? block.block_type}
          </span>
          <span
            className={
              block.block_type === "heading" ? "font-semibold text-on-surface" : "text-on-surface"
            }
          >
            {block.text}
          </span>
        </div>
      ))}
    </div>
  );
}
