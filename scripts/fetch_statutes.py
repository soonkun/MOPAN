"""국가법령정보센터에서 현행 법령 원문을 받아 업로드할 HTML 로 만든다.

    python scripts/fetch_statutes.py                 # 여덟 건 모두
    python scripts/fetch_statutes.py 특허법 실용신안법  # 이름으로 골라서
    python scripts/fetch_statutes.py --list          # 현행 MST 를 다시 조회만

받은 파일은 `data/statutes/` 에 떨어진다. 코퍼스 데이터라 커밋하지 않는다(.gitignore).
적재는 문서 화면에서 `법령` 컬렉션으로 업로드하면 된다 - 제품이 쓰는 그 경로 그대로다.
컬렉션 청킹 설정은 `일반`·`상표` 와 같은 {"preset": "korean_legal", "strategy":
"hierarchical"} 이어야 한다. 다르면 조/항 경계가 아니라 크기로 잘린다.

왜 XML 이고 왜 PDF 가 아닌가
  lawService.do 의 type=HTML 은 iframe 껍데기만 돌려주고 본문은 렌더링된 페이지
  안에 있다. type=XML 은 정부가 주는 구조화 원문이고 조문·항·호·목이 각각 자기
  CDATA 필드로 온다 - PDF 단 조판을 읽어낼 일이 없으므로 이 저장소가 한 번 겪은
  "숫자죽" 추출 사고가 원천적으로 불가능하다.

무엇을 싣고 무엇을 버리는가
  조문만 싣는다. 부칙·개정문·제개정이유는 개정 경위 서술이고, 이 코퍼스의 어떤
  인용도 그것을 가리키지 않으면서 토큰만 먹는다.
  ponytail: 별표·별지서식도 뺐다. 특허법 시행규칙의 서식이 본문보다 크고 인용은
  9건뿐이다. 서식을 가리키는 질문이 실제로 생기면 그때 별도 문서로 넣는 편이
  낫다 - 본문 청크 사이에 끼우면 조문 검색을 밀어낸다.

받고 나면 조문 수·한글 글자수를 찍고, 코퍼스가 실제로 인용하는 조(PROBES)가
안에 있는지 스스로 확인한다. 하나라도 없으면 그 줄에 "확인 필요" 가 붙는다.
"""

import argparse
import html
import pathlib
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "statutes"

SEARCH = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=XML&display=20&query={q}"
SERVICE = "https://www.law.go.kr/DRF/lawService.do?OC=test&target=law&MST={mst}&type=XML&efYd={eff}"

# 2026-09-01 에 조회한 현행 일련번호. 개정되면 --list 로 다시 뽑는다.
LAWS = {
    "특허법": ("279827", "20251111"),
    "특허법 시행령": ("277553", "20251001"),
    "특허법 시행규칙": ("286191", "20260514"),
    "실용신안법": ("277207", "20251001"),
    "실용신안법 시행령": ("277545", "20251001"),
    "상표법": ("279819", "20251111"),
    "상표법 시행령": ("277543", "20251001"),
    "상표법 시행규칙": ("287037", "20260617"),
}

# 코퍼스가 실제로 인용하는 조. 하나라도 없으면 받은 파일이 잘린 것이다.
PROBES = {
    "특허법": ["제36조", "제54조", "제81조의3"],
    "실용신안법": ["제3조", "제15조", "제20조"],
    "상표법": ["제36조", "제38조"],
}

# 장/절 제목 줄 끝의 <개정 ...> 는 메타데이터이고, 프리셋의 "제목은 마침표로 끝나지
# 않는다" 전방탐색이 걸고 넘어질 거리를 만든다. 제목에서만 떼고 조문 본문은 원문 그대로 둔다.
ANNOT = re.compile(r"\s*<(?:개정|신설|전문개정|본조신설|제목개정)[^>]*>\s*$")


def _get(url: str) -> bytes:
    return urllib.request.urlopen(url, timeout=90).read()


def _d(s: str) -> str:
    return f"{s[:4]}.{s[4:6]}.{s[6:]}" if len(s) == 8 else s


def _t(node, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


def look_up(name: str) -> tuple[str, str] | None:
    """현행 일련번호를 이름으로 다시 찾는다."""
    xml = _get(SEARCH.format(q=urllib.parse.quote(name))).decode("utf-8")
    for blk in re.findall(r"<law id=.*?</law>", xml, re.S):
        nm = re.search(r"<법령명한글><!\[CDATA\[(.*?)\]\]></법령명한글>", blk)
        cur = re.search(r"<현행연혁코드>(.*?)</현행연혁코드>", blk)
        mst = re.search(r"<법령일련번호>(\d+)</법령일련번호>", blk)
        eff = re.search(r"<시행일자>(\d+)</시행일자>", blk)
        if nm and mst and eff and cur and nm.group(1) == name and cur.group(1) == "현행":
            return mst.group(1), eff.group(1)
    return None


def build(name: str, mst: str, eff: str) -> pathlib.Path:
    """조문 XML 을 블록 하나에 조/항/호/목 한 줄씩인 HTML 로 편다.

    HtmlParser 가 <p> 하나를 블록 하나로 읽고, korean_legal 프리셋의 레벨 패턴은
    블록 첫머리에 걸린다. 그래서 한 줄 한 <p> 가 곧 프리셋이 자를 수 있는 경계다.
    """
    raw = _get(SERVICE.format(mst=mst, eff=eff))
    OUT.mkdir(parents=True, exist_ok=True)
    root = ET.fromstring(raw)
    info = root.find("기본정보")

    lines = [
        f"<h1>{html.escape(name)}</h1>",
        f"<p>[시행 {_d(_t(info, '시행일자'))}] [{html.escape(_t(info, '법종구분'))} "
        f"제{_t(info, '공포번호')}호, {_d(_t(info, '공포일자'))}, {html.escape(_t(info, '제개정구분'))}]</p>",
    ]

    articles = 0
    for u in root.findall("조문/조문단위"):
        body = _t(u, "조문내용")
        if _t(u, "조문여부") != "조문":
            head = ANNOT.sub("", body).strip()
            if head:
                lines.append(f"<p>{html.escape(head)}</p>")
            continue
        articles += 1
        if body:
            lines.append(f"<p>{html.escape(body)}</p>")
        for h in u.findall("항"):
            if _t(h, "항내용"):
                lines.append(f"<p>{html.escape(_t(h, '항내용'))}</p>")
            for ho in h.findall("호"):
                if _t(ho, "호내용"):
                    lines.append(f"<p>{html.escape(_t(ho, '호내용'))}</p>")
                for mo in ho.findall("목"):
                    if _t(mo, "목내용"):
                        lines.append(f"<p>{html.escape(_t(mo, '목내용'))}</p>")

    path = OUT / f"{name}.html"
    path.write_text(
        f'<html><head><meta charset="utf-8"><title>{html.escape(name)}</title></head><body>\n'
        + "\n".join(lines)
        + "\n</body></html>\n",
        encoding="utf-8",
    )

    joined = "\n".join(lines)
    hangul = len(re.findall(r"[가-힣]", re.sub(r"<[^>]+>", "", joined)))
    ok = True
    for probe in PROBES.get(name, []):
        if not re.search(rf"<p>{re.escape(probe)}\(", joined):
            print(f"    !! {probe} 없음 - 받은 파일이 온전하지 않다")
            ok = False
    print(
        f"{name:16} 조 {articles:4}  {path.stat().st_size / 1024:7.1f} KB  "
        f"한글 {hangul:6}자  [시행 {_d(_t(info, '시행일자'))}]{'' if ok else '   << 확인 필요'}"
    )
    return path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="비우면 여덟 건 모두")
    ap.add_argument("--list", action="store_true", help="현행 MST 를 조회만 하고 끝낸다")
    args = ap.parse_args()

    names = args.names or list(LAWS)
    unknown = [n for n in names if n not in LAWS]
    if unknown:
        print(f"모르는 법령: {unknown}\n아는 것: {list(LAWS)}")
        return 1

    if args.list:
        for n in names:
            found = look_up(n)
            mark = "" if found == LAWS[n] else f"   << 표와 다름 (표: {LAWS[n]})"
            print(f"{n:16} {found}{mark}")
        return 0

    for n in names:
        build(n, *LAWS[n])
    print(f"\n-> {OUT}  (문서 화면에서 `법령` 컬렉션으로 업로드)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
