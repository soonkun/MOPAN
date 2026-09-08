"""텍스트가 없는 PDF는 거절하지 않고 OCR로 읽는다 (2026-09-08, 출원방식심사기준 347쪽 스캔본)."""
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.rag.parsers.pdf_parser import CID_MIN_TEXT_CHARS, PdfParser, _lines_from_text

FONT = "/usr/share/fonts/truetype/nanum/NanumSquareR.ttf"


def test_lines_from_text_drops_blank_lines_and_keeps_order():
    lines = _lines_from_text("제1장 총칙\n\n  제1조 목적  \n")
    assert [l.text for l in lines] == ["제1장 총칙", "제1조 목적"]
    assert all(l.band == 0 and l.size == 0.0 for l in lines)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract 미설치")
def test_image_only_pdf_is_read_by_ocr(tmp_path):
    try:
        font = ImageFont.truetype(FONT, 36)
    except OSError:
        pytest.skip("나눔 폰트 없음")
    # 글자를 그림으로만 담은 PDF 두 쪽 - 텍스트 레이어가 없다.
    # 줄마다 다른 문장 - 같은 줄이 5쪽 넘게 반복되면 파서가 머리글로 보고 접는다.
    pages = []
    for page_no in range(2):
        image = Image.new("RGB", (1400, 1800), "white")
        draw = ImageDraw.Draw(image)
        for row in range(24):
            n = page_no * 24 + row + 1
            draw.text((80, 80 + row * 64), f"제{n}조 특허출원서에는 발명의 명칭과 출원인의 성명을 적어야 한다.", fill="black", font=font)
        pages.append(image)
    path = tmp_path / "scan.pdf"
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=200)

    parsed = PdfParser().parse(str(path))
    text = "\n".join(b.text for b in parsed.blocks)
    assert len(text) >= CID_MIN_TEXT_CHARS
    assert "출원인" in text and "발명의 명칭" in text
