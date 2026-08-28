from dataclasses import dataclass, field
from typing import Literal

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
