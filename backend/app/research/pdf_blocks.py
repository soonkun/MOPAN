# app/research/pdf_blocks.py - 원본 새싹이 이식.
"""보고서 마크다운 → 블록 목록 (CR-68).

PDF 레이아웃 계층을 마크다운 생성 로직과 **분리**하기 위한 중간 표현이다.
보고서를 만드는 쪽(`service.py`, 프롬프트)은 손대지 않고, 여기서 문서 구조만 읽어
`pdf.py`가 그 구조대로 그린다.

`fpdf2.write_html`을 버린 이유: 표 머리글 반복, 행이 페이지 경계에서 쪼개지지 않게 하기,
제목이 페이지 맨 아래 혼자 남지 않게 하기 — 전문 보고서에 필요한 이 셋을 HTML 경로에서는
제어할 수 없다. 블록으로 끊어 두면 그릴 때 각각을 판단할 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Heading:
    level: int
    text: str


@dataclass
class Paragraph:
    text: str


@dataclass
class ListBlock:
    items: list[tuple[int, str]] = field(default_factory=list)  # (들여쓰기 단계, 본문)
    ordered: bool = False


@dataclass
class Table:
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Divider:
    pass


Block = Heading | Paragraph | ListBlock | Table | Divider

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$")


def _cells(line: str) -> list[str]:
    inner = _TABLE_ROW.match(line)
    raw = inner.group(1) if inner else line
    return [c.strip() for c in raw.split("|")]


def parse_blocks(md: str) -> list[Block]:
    """마크다운을 블록으로 끊는다. 인라인 강조(`**`)는 그대로 두고 렌더러가 처리한다."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    para: list[str] = []
    lst: ListBlock | None = None

    def flush_para() -> None:
        nonlocal para
        if para:
            blocks.append(Paragraph(" ".join(x.strip() for x in para).strip()))
            para = []

    def flush_list() -> None:
        nonlocal lst
        if lst and lst.items:
            blocks.append(lst)
        lst = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para()
            flush_list()
            i += 1
            continue

        if _HR.match(stripped):
            flush_para()
            flush_list()
            blocks.append(Divider())
            i += 1
            continue

        m = _HEADING.match(stripped)
        if m:
            flush_para()
            flush_list()
            blocks.append(Heading(len(m.group(1)), m.group(2).strip()))
            i += 1
            continue

        # 표 — 머리글 줄 + 구분선이 이어질 때만 표로 본다.
        if _TABLE_ROW.match(line) and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            flush_list()
            table = Table(header=_cells(line))
            i += 2
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                table.rows.append(_cells(lines[i]))
                i += 1
            # 열 수를 머리글에 맞춰 고른다 — 모자라면 빈 칸, 넘치면 자른다.
            width = len(table.header)
            table.rows = [(r + [""] * width)[:width] for r in table.rows]
            blocks.append(table)
            continue

        mb = _BULLET.match(line)
        mo = _ORDERED.match(line)
        if mb or mo:
            flush_para()
            mm = mb or mo
            assert mm is not None
            depth = len(mm.group(1)) // 2
            ordered = mo is not None
            if lst is None or lst.ordered != ordered:
                flush_list()
                lst = ListBlock(ordered=ordered)
            lst.items.append((depth, mm.group(2).strip()))
            i += 1
            continue

        flush_list()
        para.append(stripped)
        i += 1

    flush_para()
    flush_list()
    return blocks


__all__ = ["Block", "Divider", "Heading", "ListBlock", "Paragraph", "Table", "parse_blocks"]
