import re
from dataclasses import dataclass, field
from typing import Literal

# A classification-table section header: a bracketed 니스 class + 유사군코드, the
# shape 유사상품 심사기준 opens every one of its ~930 sections with
# ("[제9류/G390802] 소프트웨어", "[제35류/S120602] 광고업", "[제9류/G3902, G3903]").
# Lives here rather than in either user because BOTH need the same one: the PDF
# parser to emit such a line as a heading instead of burying it in a paragraph,
# and the chunking layer to decide a document is a classification table and to
# cut it on these boundaries. Two copies would be two things to keep in step.
CLASS_GROUP_MARKER = re.compile(
    r"^\[제\s*\d+\s*류\s*/\s*[A-Z]\d{3,}(?:\s*,\s*[A-Z]\d{3,})*\s*\]"
)

BlockType = Literal["heading", "paragraph", "list_item", "table_cell"]


@dataclass
class Block:
    text: str
    block_type: BlockType
    page: int | None = None
    section: str | None = None


@dataclass
class ParsedDocument:
    blocks: list[Block] = field(default_factory=list)
