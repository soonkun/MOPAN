import pytest
from docx import Document as DocxDocument

from app.rag.parsers import get_parser
from app.rag.parsers.pdf_parser import _is_heading


def _write_pdf(path, pages_lines: list[list[str]]) -> None:
    """Minimal text-only PDF writer. No PDF-authoring library is installed and
    adding one just for fixtures is not worth it - the parsers must be proven
    against bytes pypdf actually reads, not against a mocked extract_text."""
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    page_ids = [5 + 2 * i for i in range(len(pages_lines))]
    objs[2] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (
        len(pages_lines),
        b" ".join(b"%d 0 R" % p for p in page_ids),
    )
    for i, lines in enumerate(pages_lines):
        parts = [b"BT", b"/F1 12 Tf", b"1 0 0 1 72 720 Tm", b"16 TL"]
        for line in lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            parts.append(b"(" + escaped.encode("latin-1") + b") Tj T*")
        stream = b"\n".join([*parts, b"ET"])
        objs[4 + 2 * i] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        objs[5 + 2 * i] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (4 + 2 * i)
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref_offset, size = len(out), max(objs) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for num in range(1, size):
        out += b"%010d 00000 n \n" % offsets.get(num, 0)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_offset)
    path.write_bytes(bytes(out))


def test_text_parser_detects_headings_and_lists(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# Title\n\nSome paragraph.\n\n- item one\n- item two\n", encoding="utf-8")

    parsed = get_parser("md").parse(str(path))

    assert parsed.blocks[0].block_type == "heading"
    assert parsed.blocks[0].text == "Title"
    assert any(b.block_type == "list_item" and b.text == "item one" for b in parsed.blocks)
    assert all(b.section == "Title" for b in parsed.blocks[1:])


def test_html_parser_extracts_headings_paragraphs_lists_and_cells(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text(
        "<h1>Intro</h1><p>Hello world</p><ul><li>a point</li></ul>"
        "<table><tr><td>cell</td></tr></table><script>ignored()</script>",
        encoding="utf-8",
    )

    parsed = get_parser("html").parse(str(path))
    types = {b.block_type for b in parsed.blocks}

    assert parsed.blocks[0].block_type == "heading"
    assert {"heading", "paragraph", "list_item", "table_cell"} <= types
    assert not any("ignored" in b.text for b in parsed.blocks)


def test_html_parser_keeps_words_apart_around_inline_markup(tmp_path):
    """get_text(strip=True) concatenates the stripped pieces, so real-world
    markup ('Hello <b>world</b>') comes back as 'Helloworld' - unsearchable."""
    path = tmp_path / "doc.html"
    path.write_text("<p>Hello <b>world</b> and <i>friends</i></p>", encoding="utf-8")

    [block] = get_parser("html").parse(str(path)).blocks

    assert block.text == "Hello world and friends"


def test_html_parser_does_not_emit_nested_tags_twice(tmp_path):
    """<td><p>x</p></td> must yield one block, not the same text as both a
    table_cell and a paragraph - duplicates get indexed and retrieved twice."""
    path = tmp_path / "doc.html"
    path.write_text("<table><tr><td><p>only once</p></td></tr></table>", encoding="utf-8")

    blocks = get_parser("html").parse(str(path)).blocks

    assert [(b.text, b.block_type) for b in blocks] == [("only once", "table_cell")]


def test_pdf_heading_heuristic_accepts_real_headings():
    assert _is_heading("3.2 Results", "Body text follows.") is True
    assert _is_heading("METHODOLOGY", "Body text follows.") is True
    assert _is_heading("Executive Summary", "") is True


def test_pdf_heading_heuristic_accepts_separated_and_multilevel_numbers():
    assert _is_heading("1. Introduction", "Body text follows.") is True
    assert _is_heading("4) Methods", "Body text follows.") is True


def test_pdf_heading_heuristic_rejects_wrapped_body_lines():
    # A wrapped sentence fragment also lacks terminal punctuation - lower-cased
    # words are what keep this from becoming a heading.
    assert _is_heading("the results of the experiment were", "consistent across runs.") is False
    assert _is_heading("This is a complete sentence.", "") is False
    assert _is_heading("x" * 120, "") is False


def test_pdf_heading_heuristic_rejects_numeric_leading_prose():
    """A bare leading number matched before, so a year- or count-leading
    sentence became current_section and relabelled every citation after it."""
    assert _is_heading("2025 was a strong year for the company", "Revenue rose.") is False
    assert _is_heading("15 growers reported blight in June", "Most recovered.") is False
    assert _is_heading("3 of the 12 plots were affected", "The rest were clean.") is False


def test_pdf_parser_emits_headings_with_pages_and_sections(tmp_path):
    """pypdf never emits blank lines between lines of a page, so heading
    detection has to survive on the text alone."""
    path = tmp_path / "doc.pdf"
    _write_pdf(
        path,
        [
            [
                "ANNUAL REPORT",
                "1. Introduction",
                "This document describes the operating results for the fiscal",
                "year and comments on the segments that grew most.",
                "Executive Summary",
                "Revenue grew twelve percent year over year, driven primarily",
                "by the enterprise segment.",
            ],
            ["3.2 Results", "The results were consistent across all runs."],
        ],
    )

    parsed = get_parser("pdf").parse(str(path))
    headings = [b for b in parsed.blocks if b.block_type == "heading"]

    assert [b.text for b in headings] == [
        "ANNUAL REPORT",
        "1. Introduction",
        "Executive Summary",
        "3.2 Results",
    ]
    assert [b.page for b in headings] == [1, 1, 1, 2]
    body = [b for b in parsed.blocks if b.block_type == "paragraph"]
    assert body[0].section == "1. Introduction"
    assert body[-1].section == "3.2 Results"
    assert body[-1].page == 2


def test_pdf_parser_without_headings_yields_one_block_per_page(tmp_path):
    """No structure to find, so the page is the boundary - and the page number
    survives for citations. Task 9's size pass splits these further."""
    path = tmp_path / "plain.pdf"
    _write_pdf(
        path,
        [
            ["Tomato blight spreads through infected soil and splashing water.", "It is bad."],
            ["Growers should rotate crops and remove all infected plant debris.", "So do that."],
        ],
    )

    blocks = get_parser("pdf").parse(str(path)).blocks

    assert [b.block_type for b in blocks] == ["paragraph", "paragraph"]
    assert [b.page for b in blocks] == [1, 2]
    assert blocks[0].text.startswith("Tomato blight")


def test_docx_parser_reads_styles_and_tables(tmp_path):
    path = tmp_path / "doc.docx"
    document = DocxDocument()
    document.add_heading("Quarterly Report", level=0)
    document.add_heading("Overview", level=1)
    document.add_paragraph("Revenue grew twelve percent.")
    document.add_paragraph("first bullet", style="List Bullet")
    document.add_paragraph("")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "APAC"
    table.cell(0, 1).text = "120"
    document.save(str(path))

    blocks = get_parser("docx").parse(str(path)).blocks

    assert [(b.text, b.block_type) for b in blocks] == [
        ("Quarterly Report", "heading"),
        ("Overview", "heading"),
        ("Revenue grew twelve percent.", "paragraph"),
        ("first bullet", "list_item"),
        ("APAC", "table_cell"),
        ("120", "table_cell"),
    ]
    assert blocks[2].section == "Overview"


def test_docx_parser_emits_a_merged_cell_once(tmp_path):
    """row.cells repeats the same <w:tc> once per spanned grid column, so a
    merged header cell would be embedded, indexed and retrieved three times."""
    path = tmp_path / "merged.docx"
    document = DocxDocument()
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 0).merge(table.cell(0, 2)).text = "Regional Summary"
    table.cell(1, 0).text = "APAC"
    table.cell(1, 1).text = "120"
    table.cell(1, 2).text = "up"
    document.save(str(path))

    cells = [b.text for b in get_parser("docx").parse(str(path)).blocks if b.block_type == "table_cell"]

    assert cells == ["Regional Summary", "APAC", "120", "up"]


@pytest.mark.parametrize("file_type,name", [("txt", "a.txt"), ("html", "a.html"), ("pdf", "a.pdf")])
def test_parsers_raise_file_not_found_for_a_missing_file(tmp_path, file_type, name):
    with pytest.raises(FileNotFoundError):
        get_parser(file_type).parse(str(tmp_path / name))


def test_docx_parser_raises_file_not_found_for_a_missing_file(tmp_path):
    """python-docx raises PackageNotFoundError for a missing file, which is not
    a FileNotFoundError - the structure endpoint's 404 branch would miss it."""
    with pytest.raises(FileNotFoundError):
        get_parser("docx").parse(str(tmp_path / "gone.docx"))


def test_get_parser_raises_for_unsupported_type():
    with pytest.raises(ValueError):
        get_parser("exe")
