"""회의 결과보고 한글 문서(hwpx). 원본 saessagi src/meeting_minutes/hwpx_writer.py 이식.

template.hwpx(새싹이의 "회의 결과보고 템플릿")의 {{…}} 문단을 줄마다 복제해 채운다. hwpx = zip + XML.
원본과 다른 점: 모델이 만든 JSON이 아니라 화면의 글을 읽은 Minutes(draft.parse)를 그대로 옮긴다 - 본 대로 나온다.
"""
from __future__ import annotations

import copy
import io
import re
import zipfile
from pathlib import Path

from lxml import etree

from app.minutes.draft import Minutes

TEMPLATE = Path(__file__).with_name("template.hwpx")
SECTION = "Contents/section0.xml"
PREFIX = {"o": "○ ", "dash": "  - ", "star": "   * "}
# 구획 → 꼭지 종류별 본보기 문단. 향후계획은 양식에 ○뿐이라 -·*는 주요내용 것을 빌린다.
SLOTS = {
    "개요": {"o": "SUMMARY_O", "dash": "SUMMARY_DASH", "star": "SUMMARY_STAR"},
    "주요내용": {"o": "DETAIL_O", "dash": "DETAIL_DASH", "star": "DETAIL_STAR"},
    "향후계획": {"o": "NEXT_O", "dash": "DETAIL_DASH", "star": "DETAIL_STAR"},
}


def build(m: Minutes, date: str, department: str = "") -> bytes:
    with zipfile.ZipFile(TEMPLATE) as source:
        files = {info.filename: source.read(info.filename) for info in source.infolist()}
    root = etree.fromstring(files[SECTION])
    ns = {"hp": root.nsmap["hp"]}

    def text_of(p) -> str:
        return "".join(t.text or "" for t in p.iterfind(".//hp:t", ns))

    def put(p, text: str) -> None:
        first, *rest = p.findall(".//hp:t", ns)
        first.text = text
        for t in rest:
            t.text = ""

    # 문서 순서로 돌아 안쪽 문단이 나중에 덮어쓴다(표 칸 안 문단은 바깥 문단의 글에도 잡힌다).
    slot = {key: p for p in root.iterfind(".//hp:p", ns) for key in re.findall(r"\{\{(\w+)\}\}", text_of(p))}
    put(slot["TITLE"], m.title)
    put(slot["DATE_DEPT"], f"{date} {department}".strip())
    put(slot["DT_PLACE"], f"○ 일시·장소 : {m.when or date}")
    put(slot["ATTENDEES"], f"○ 참 석 자 : {m.attendees or '-'}")
    for section, keys in SLOTS.items():
        for level, body in m.sections[section]:
            node = copy.deepcopy(slot[keys[level]])
            put(node, PREFIX[level] + body)
            slot[keys["o"]].addprevious(node)
    for key in {k for keys in SLOTS.values() for k in keys.values()}:
        slot[key].getparent().remove(slot[key])
    for section in ("주요내용", "향후계획"):  # 내용 없는 구획은 제목 문단도 뺀다
        if not m.sections[section]:
            header = next((p for p in root.iterfind(".//hp:p", ns) if section in text_of(p) and len(text_of(p).strip()) <= len(section) + 20), None)
            if header is not None:
                header.getparent().remove(header)

    files[SECTION] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        # mimetype은 첫 항목·무압축이어야 한글이 연다.
        target.writestr(zipfile.ZipInfo("mimetype"), files.pop("mimetype"), compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            target.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()
