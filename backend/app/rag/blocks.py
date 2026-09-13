import re
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


# Postgres의 text는 NUL(0x00)을 받지 않는다 - 실사고(2026-09-13): 완결보고서 PDF 12,107건 중 296건이
# "invalid byte sequence for encoding UTF8: 0x00"으로 색인 실패. 글꼴 매핑이 깨진 PDF의 추출
# 텍스트에 NUL이 섞여 나온다. 다른 C0 제어문자도 검색·표시에 쓸모가 없어 함께 지운다(개행·탭은 남긴다).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(text: str | None) -> str | None:
    return _CONTROL.sub("", text) if text else text


def sanitize(parsed: "ParsedDocument") -> "ParsedDocument":
    """파서가 낸 블록의 본문·섹션에서 제어문자를 지운다. 파서마다 넣지 않고 파이프라인 한 곳에서."""
    for block in parsed.blocks:
        block.text = clean_text(block.text) or ""
        block.section = clean_text(block.section)
    return parsed
