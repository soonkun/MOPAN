# app/research/pdf.py - 원본 새싹이 src/deep_research/pdf.py 이식(CR-67·68). 표현 계층만.
"""보고서 마크다운 → PDF (CR-67 도입 · CR-68 레이아웃 개편).

**왜 서버에서 만드는가.** 예전에는 브라우저 인쇄로 만들었다(숨은 iframe + `window.print()`).
iOS Safari는 그 iframe이 아니라 **화면 전체**를 인쇄해서, 보고서 대신 내비게이션·버튼·
입력창이 담긴 앱 화면이 1페이지로 찍혔다.

**왜 `write_html`을 버렸나 (CR-68).** 전문 보고서에 필요한 세 가지를 HTML 경로에서는
제어할 수 없다 — 표 머리글의 다음 장 반복, 행이 페이지 경계에서 쪼개지지 않게 하기,
제목이 페이지 맨 아래 혼자 남지 않게 하기. 기본 서식이 제목·불릿을 진한 빨강으로
칠하는 문제도 있었다.

**마크다운 생성 로직은 건드리지 않는다.** 여기는 표현 계층이다. 보고서를 만드는 쪽
(`service.py`·프롬프트)은 그대로 두고 `pdf_blocks.parse_blocks()`로 구조만 읽어 그린다.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .pdf_blocks import Divider, Heading, ListBlock, Paragraph, Table, parse_blocks

logger = logging.getLogger(__name__)

# ── 규격 ──────────────────────────────────────────────────────────────────────
# 사용자가 정한 범위 안에서 골랐다. 한 곳에 모아 두어야 조정이 쉽다.

MARGIN_X = 22.0  # mm 좌우 (요구 20~24)
MARGIN_TOP = 21.0  # mm (요구 20~22)
MARGIN_BOTTOM = 21.0  # mm (요구 20~22)
MAX_TEXT_WIDTH = 150.0  # mm — 한 줄이 지나치게 길어지지 않게 본문 폭 상한

FS_BODY = 10.0  # pt (요구 9.5~10.5)
FS_TITLE = 21.0  # pt (요구 20~24)
FS_H1 = 15.5  # pt (요구 15~17)
FS_H2 = 12.5  # pt (요구 12~14)
FS_H3 = 11.0
FS_TABLE = 9.0  # pt (요구 8.5~9.5)
FS_META = 8.5

LINE_RATIO = 1.62  # line-height (요구 1.55~1.7)

# 완전한 검정보다 부드러운 dark gray
COLOR_TEXT = (38, 38, 42)
COLOR_HEADING = (22, 22, 26)
COLOR_MUTED = (118, 118, 126)
COLOR_RULE = (208, 208, 214)
COLOR_TABLE_HEAD_BG = (243, 243, 245)

# 제목 위/아래 여백 (mm). 요구: 위 24~32px(≈6.3~8.5mm), 아래 10~16px(≈2.6~4.2mm)
H_TOP = {1: 8.0, 2: 6.6, 3: 5.4}
H_BOTTOM = {1: 3.6, 2: 3.0, 3: 2.6}

_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    (
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ),
    (
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
    ),
)


class PdfUnavailable(RuntimeError):
    """PDF를 만들 수 없는 상태 (폰트 없음·라이브러리 없음)."""


def _find_fonts() -> tuple[str, str]:
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).exists():
            return regular, bold if Path(bold).exists() else regular
    raise PdfUnavailable(
        "한글 폰트를 찾지 못했습니다 (나눔고딕). PDF 대신 MD 내려받기를 사용하세요."
    )


# ── 본문 정리 ─────────────────────────────────────────────────────────────────

_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def strip_latex(md: str) -> str:
    """모델이 섞어 내는 LaTeX을 읽을 수 있는 글자로 (`$\\text{CO}_2$` → `CO₂`)."""

    def _inner(m: re.Match[str]) -> str:
        t = m.group(1)
        t = re.sub(r"\\text\{([^}]*)\}", r"\1", t)
        t = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", t)
        t = t.replace("^\\circ", "°").replace("\\circ", "°")
        t = t.replace("\\%", "%").replace("\\times", "×")
        t = re.sub(r"\^\{?(\w+)\}?", r"\1", t)
        t = re.sub(r"_\{?(\d+)\}?", lambda k: k.group(1).translate(_SUB), t)
        t = re.sub(r"\\[a-zA-Z]+", "", t)
        return t.replace("{", "").replace("}", "").strip()

    out = re.sub(r"\$\$(.+?)\$\$", _inner, md or "", flags=re.S)
    return re.sub(r"\$([^$\n]+?)\$", _inner, out)


def strip_citation_markers(md: str) -> str:
    """내부 인용 표기를 뺀다 — 배포 문서에 남으면 안 된다."""
    out = re.sub(r"\[\[(?:doc|note):.+?\]\]", "", md or "", flags=re.S)
    return re.sub(r"[ \t]{2,}", " ", out)


def drop_leading_h1(md: str) -> str:
    """머리 제목과 겹치는 본문 첫 H1을 뺀다 — 제목이 두 번 나왔다."""
    return re.sub(r"\A\s*#\s+[^\n]*\n+", "", md or "", count=1)


def _clean_inline(text: str) -> str:
    """fpdf2 `markdown=True`가 못 읽는 표기를 정리한다.

    fpdf2는 `**굵게**`·`__밑줄__`·`--기울임--`만 안다. 남은 기호는 글자로 보이므로
    미리 없앤다. 특히 `--`는 fpdf2가 기울임으로 먹으므로 en dash로 바꾼다.
    """
    t = re.sub(r"`([^`]*)`", r"\1", text or "")  # 인라인 코드
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)  # 링크 → 글자만
    t = re.sub(r"(?<!-)--(?!-)", "–", t)
    return t.strip()


# ── 렌더러 ────────────────────────────────────────────────────────────────────


class _Report:
    """블록을 A4 보고서로 그린다."""

    def __init__(self, pdf: Any) -> None:
        self.pdf = pdf
        self.body_w = min(MAX_TEXT_WIDTH, pdf.w - MARGIN_X * 2)

    def _line_h(self, size_pt: float) -> float:
        return size_pt * LINE_RATIO * 0.3528  # pt → mm

    def _space_left(self) -> float:
        return self.pdf.h - MARGIN_BOTTOM - self.pdf.get_y()

    def header(self, kicker: str, title: str, meta: str) -> None:
        """머리 영역 — 작은 분류, 큰 제목, 보조정보, 얇은 divider."""
        from fpdf.enums import XPos, YPos

        pdf = self.pdf
        if kicker:
            pdf.set_font("ko", "B", FS_META + 0.5)
            pdf.set_text_color(*COLOR_MUTED)
            pdf.multi_cell(
                self.body_w,
                self._line_h(FS_META),
                kicker,
                align="L",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.ln(1.6)

        pdf.set_font("ko", "B", FS_TITLE)
        pdf.set_text_color(*COLOR_HEADING)
        pdf.multi_cell(
            self.body_w,
            FS_TITLE * 1.28 * 0.3528,
            title,
            align="L",  # 제목이 양쪽정렬로 벌어지면 낱말 사이가 성글어 보인다
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.ln(2.2)

        if meta:
            pdf.set_font("ko", "", FS_META)
            pdf.set_text_color(*COLOR_MUTED)
            pdf.multi_cell(
                self.body_w,
                self._line_h(FS_META),
                meta,
                align="L",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.ln(2.4)

        self.rule()
        pdf.ln(4.0)

    def rule(self) -> None:
        pdf = self.pdf
        pdf.set_draw_color(*COLOR_RULE)
        pdf.set_line_width(0.2)
        y = pdf.get_y()
        pdf.line(MARGIN_X, y, MARGIN_X + self.body_w, y)
        pdf.set_y(y)

    def heading(self, level: int, text: str) -> None:
        from fpdf.enums import XPos, YPos

        pdf = self.pdf
        size = {1: FS_H1, 2: FS_H2}.get(level, FS_H3)
        top = H_TOP.get(level, 4.6)
        bottom = H_BOTTOM.get(level, 2.4)
        lh = self._line_h(size)

        # 제목이 페이지 맨 아래 혼자 남지 않게 — 제목 + 다음 두 줄 자리가 없으면 넘긴다.
        if self._space_left() < top + lh + self._line_h(FS_BODY) * 2:
            pdf.add_page()
        else:
            pdf.ln(top)

        pdf.set_font("ko", "B", size)
        pdf.set_text_color(*COLOR_HEADING)
        pdf.multi_cell(
            self.body_w, lh, _clean_inline(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )
        pdf.ln(bottom)

    def paragraph(self, text: str) -> None:
        from fpdf.enums import XPos, YPos

        pdf = self.pdf
        pdf.set_font("ko", "", FS_BODY)
        pdf.set_text_color(*COLOR_TEXT)
        pdf.multi_cell(
            self.body_w,
            self._line_h(FS_BODY),
            _clean_inline(text),
            markdown=True,
            align="L",  # 기본 양쪽정렬은 낱말 사이를 벌려 한글 가독성을 해친다
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.ln(1.8)

    def list_block(self, block: ListBlock) -> None:
        from fpdf.enums import XPos, YPos

        pdf = self.pdf
        lh = self._line_h(FS_BODY)
        for n, (depth, text) in enumerate(block.items, 1):
            indent = 3.5 + depth * 4.5
            marker = f"{n}." if block.ordered else ("•" if depth == 0 else "–")
            pdf.set_font("ko", "", FS_BODY)
            pdf.set_text_color(*COLOR_MUTED)
            pdf.set_x(MARGIN_X + indent)
            pdf.cell(5.0, lh, marker)
            pdf.set_text_color(*COLOR_TEXT)
            pdf.set_x(MARGIN_X + indent + 5.0)
            pdf.multi_cell(
                self.body_w - indent - 5.0,
                lh,
                _clean_inline(text),
                markdown=True,
                align="L",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.ln(0.6)
        pdf.ln(1.4)

    def table(self, block: Table) -> None:
        from fpdf.enums import TableBordersLayout, TableCellFillMode
        from fpdf.fonts import FontFace

        pdf = self.pdf
        if not block.header and not block.rows:
            return
        pdf.ln(1.6)

        rows = ([block.header] if block.header else []) + block.rows
        widths = _column_widths(rows)

        pdf.set_font("ko", "", FS_TABLE)
        pdf.set_text_color(*COLOR_TEXT)
        pdf.set_draw_color(*COLOR_RULE)
        pdf.set_line_width(0.15)
        with pdf.table(
            # 세로선 없이 가로선만 — 진한 격자는 보고서에 무겁다.
            borders_layout=TableBordersLayout.HORIZONTAL_LINES,
            col_widths=widths,
            width=self.body_w,
            text_align="LEFT",
            line_height=self._line_h(FS_TABLE),
            padding=(2.0, 2.4, 2.0, 2.4),
            markdown=True,
            first_row_as_headings=bool(block.header),
            repeat_headings=1,  # 다음 장에도 머리글을 다시 그린다
            headings_style=FontFace(
                emphasis="B", color=COLOR_HEADING, fill_color=COLOR_TABLE_HEAD_BG
            ),
            cell_fill_mode=TableCellFillMode.NONE,
        ) as table:
            for r in rows:
                row = table.row()
                for cell in r:
                    row.cell(_clean_inline(cell))
        pdf.ln(3.2)

    def divider(self) -> None:
        self.pdf.ln(2.4)
        self.rule()
        self.pdf.ln(3.0)


def _column_widths(rows: list[list[str]]) -> tuple[float, ...]:
    """열 폭을 내용 길이에 맞춰 정한다.

    fpdf2 기본은 균등 분할이라 `[1]` 같은 짧은 열이 긴 문장과 같은 폭을 먹었다.
    글자 수 평균으로 비율을 잡되 한 열이 지나치게 좁거나 넓지 않게 가둔다.
    """
    if not rows:
        return ()
    n = max(len(r) for r in rows)
    header = rows[0] if rows else []
    weights: list[float] = []
    for c in range(n):
        lens = [len(r[c]) for r in rows[1:] if c < len(r)] or [0]
        body_avg = sum(lens) / len(lens)
        head_len = len(header[c]) if c < len(header) else 0
        # 머리글이 본문보다 길면 머리글에 맞춘다. 한글은 낱말 안에서 줄바꿈되므로
        # 열이 좁으면 `비/교/적/합/성`처럼 **한 글자씩 세로로 쪼개진다**.
        weights.append(max(body_avg, head_len * 1.15, 3.0))
    total = sum(weights)
    shares = [w / total for w in weights]
    # 최소 몫을 넉넉히 준다 — 좁은 열이 세로로 쪼개지는 것을 막는 것이 우선이다.
    floor = min(0.13, 0.9 / n)
    clamped = [min(max(x, floor), 0.46) for x in shares]
    s = sum(clamped)
    return tuple(round(x / s * 100, 2) for x in clamped)


def _title_without_kicker(title: str, kicker: str) -> str:
    """제목 앞의 `(분류)` 접두를 뗀다.

    프론트가 만드는 제목이 `(과제 중복성 검토)미래기후 시나리오…` 형태인데, 분류줄을
    따로 두면서 같은 말이 두 번 나온다.
    """
    t = (title or "보고서").strip()
    k = (kicker or "").strip()
    if k and t.startswith("("):
        head, sep, rest = t[1:].partition(")")
        if sep and head.strip() == k:
            return rest.strip() or t
    return t


def report_to_pdf(title: str, markdown_text: str, meta: str = "", kicker: str = "") -> bytes:
    """보고서 한 건을 PDF 바이트로. 실패하면 `PdfUnavailable`."""
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover
        raise PdfUnavailable(f"PDF 라이브러리를 불러올 수 없습니다: {exc}") from exc

    regular, bold = _find_fonts()
    body_md = drop_leading_h1(strip_latex(strip_citation_markers(markdown_text)))
    blocks = parse_blocks(body_md)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(MARGIN_X, MARGIN_TOP, MARGIN_X)
    pdf.set_auto_page_break(auto=True, margin=MARGIN_BOTTOM)
    pdf.add_font("ko", "", regular)
    pdf.add_font("ko", "B", bold)
    pdf.set_font("ko", "", FS_BODY)
    pdf.add_page()

    doc = _Report(pdf)
    doc.header(kicker, _title_without_kicker(title, kicker), meta)

    for block in blocks:
        if isinstance(block, Heading):
            doc.heading(block.level, block.text)
        elif isinstance(block, Paragraph):
            doc.paragraph(block.text)
        elif isinstance(block, ListBlock):
            doc.list_block(block)
        elif isinstance(block, Table):
            doc.table(block)
        elif isinstance(block, Divider):
            doc.divider()

    data = bytes(pdf.output())
    logger.info(
        "보고서 PDF 생성: %s (블록 %d · %d바이트)", (title or "")[:40], len(blocks), len(data)
    )
    return data


__all__ = ["PdfUnavailable", "report_to_pdf", "strip_citation_markers", "strip_latex"]
