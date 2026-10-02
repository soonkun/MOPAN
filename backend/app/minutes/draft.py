"""회의 결과보고서 초안 - 받아 적은 글 → 개조식 글 → (점검 → 고쳐 쓰기) → 점검 결과.

원본 새싹이와 다른 점(작성 지침이 더 잘 지켜지게):
- 녹취록을 8천 자씩 잘라 요약한 뒤 합치지 않고 통째로 한 번에 읽는다(모델 창이 넉넉하다) - 잘라 요약하면 수치가 빠진다.
- 쓴 글을 코드가 지침대로 잰다(lint: 구획·꼭지 수·글자 수·종결·날짜 형식·녹취에 없는 숫자). 모델은 글자를 못 센다.
- 검토자 모델이 녹취록과 대조해 누락·근거 없는 내용을 찾고, 두 결과를 주고 고쳐 쓰게 한다(최대 MAX_REVISIONS번).
- 한글 문서는 화면에서 보고 고친 그 글을 parse()로 읽어 만든다 - 모델이 한 번 더 요약해 JSON으로 옮기지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.llm.base import ChatMessage, LLMProvider
from app.minutes.prompts import LAYOUT, REVIEW, VOLUME

SECTIONS = ("개요", "주요내용", "향후계획")
# 구획별 ○ 개수(쪽수별). prompts.VOLUME과 같은 숫자.
RANGE = {1: {"개요": (1, 1), "주요내용": (2, 3), "향후계획": (1, 2)}, 2: {"개요": (1, 2), "주요내용": (7, 8), "향후계획": (2, 3)}}
LINE, TWO_LINES = 37, (70, 72)  # 한 줄 / 두 줄 글자 수(띄어쓰기 포함) - 지침의 숫자
MAX_REVISIONS = 2
LEVEL_NAME = {"o": "○", "dash": "-", "star": "*"}


@dataclass
class Minutes:
    title: str = ""
    when: str = ""       # "○ 일시·장소 :" 뒤
    attendees: str = ""  # "○ 참 석 자 :" 뒤
    sections: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: {s: [] for s in SECTIONS})  # (o|dash|star, 글)


_HEADER = re.compile(r"^\[?\s*(개요|주요\s*내용|향후\s*계획)\s*\]?$")
_MARK = re.compile(r"^([○〇◯]|-|\*)\s*(.*)$")
_WHEN = re.compile(r"^일\s*시\s*[·ㆍ‧∙,및 ]*장\s*소\s*[:：]\s*(.*)$")
_WHO = re.compile(r"^참\s*석\s*자\s*[:：]\s*(.*)$")
_STAMP = re.compile(r"^\[\d+:\d+(?::\d+)?\]\s*", re.M)


def parse(text: str) -> Minutes:
    """LAYOUT 모양의 글을 읽는다. 사람이 고친 글도 받으므로 너그럽게: 기호 없는 줄은 앞 꼭지에 이어 붙인다."""
    m, section = Minutes(), None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        header = _HEADER.match(line)
        if header:
            section = header.group(1).replace(" ", "")
            continue
        mark = _MARK.match(line)
        if section is None:
            m.title = m.title or (mark.group(2) if mark else line)
            continue
        items = m.sections[section]
        if not mark:
            if items:
                items[-1] = (items[-1][0], f"{items[-1][1]} {line}")
            continue
        level, body = {"-": "dash", "*": "star"}.get(mark.group(1), "o"), mark.group(2).strip()
        if section == "개요" and level == "o" and (found := _WHEN.match(body)):
            m.when = found.group(1).strip()
        elif section == "개요" and level == "o" and (found := _WHO.match(body)):
            m.attendees = found.group(1).strip()
        elif body:
            items.append((level, body))
    return m


_SENTENCE = re.compile(r"(니다|어요|아요|해요|세요|했다|하였다|한다|된다|됐다|이다|있다|없다|였다|겠다)[.!]?$")
_NOMINAL = re.compile(r"[함음임됨]\.?$")
_DUE = re.compile(r"\s*\(([^()]*)\)$")
_DIGITS = re.compile(r"\d+")


def _digits(text: str) -> set[str]:
    """글 속의 숫자들. 앞의 0은 뗀다 - 보고서의 "11.02."와 녹취록의 "11월 2일"은 같은 숫자다."""
    return {d.lstrip("0") or "0" for d in _DIGITS.findall(text.replace(",", ""))}


def lint(text: str, transcript: str = "", pages: int = 1, today: str = "") -> list[dict]:
    """지침 가운데 셀 수 있는 것을 잰다. 녹취록이 없으면 숫자 대조는 건너뛴다.
    level: must(지침 위반) | should(지침의 권장을 벗어남) - 둘은 모델에게 고쳐 쓰게 한다 | check(사람이 확인할 것)."""
    issues: list[dict] = []

    def add(level: str, message: str, line: str = "") -> None:
        issues.append({"level": level, "line": line, "message": message})

    if re.search(r"\*\*|```|^#|^\s*[{\[\"].*\":", text, re.M):
        add("must", "마크다운·JSON 기호(#, **, ```, 중괄호)가 들어 있음 - 개조식 글만 남길 것")
    m = parse(text)
    if not m.title:
        add("must", "첫 줄에 회의 제목이 없음")
    if m.when and re.search(r"\d", m.when) and not re.search(r"\d{4}\.\d{1,2}\.\d{1,2}\.", m.when):
        add("must", "일시는 YYYY.MM.DD. 형식(마지막 마침표 포함)으로 쓸 것", m.when)
    spoken = _digits(_STAMP.sub("", transcript)) | _digits(today)
    marked: list[str] = []
    for section in SECTIONS:
        items = m.sections[section]
        low, high = RANGE[pages][section]
        count = sum(level == "o" for level, _ in items)
        if count == 0:
            add("must", f"[{section}]에 ○ 꼭지가 없음" + (" - 회의 목적을 첫 ○로 쓸 것" if section == "개요" else ""))
        elif count > high:
            add("must", f"[{section}] ○가 {count}개 - {pages}쪽 분량은 {low}~{high}개")
        elif count < low:
            add("check", f"[{section}] ○가 {count}개 - {pages}쪽 분량은 {low}~{high}개(논의가 적었다면 그대로 둬도 됨)")
        dashes = 0
        for index, (level, body) in enumerate(items):
            shown = f"{LEVEL_NAME[level]} {body}"
            if index == 0 and level != "o":
                add("must", f"[{section}]이 ○ 없이 {LEVEL_NAME[level]} 로 시작함", shown)
            dashes = 0 if level == "o" else dashes + (level == "dash")
            if level == "dash" and dashes in (3, 5):  # 3번째에 권장, 5번째에 금지를 한 번씩만 알린다
                add("must" if dashes == 5 else "should", "한 ○ 아래 - 가 4개를 넘음 - 관련 내용을 합쳐 줄일 것" if dashes == 5
                    else "한 ○ 아래 - 가 3개 이상 - 2개 이하 권장(두 줄짜리 - 로 묶기)", shown)
            due = _DUE.search(body) if section == "향후계획" and level == "o" else None
            core = body[: due.start()] if due else body
            if due and re.search(r"\d", due.group(1)) and not re.fullmatch(r"\d{1,2}\.\d{1,2}\.", due.group(1)):
                add("must", "향후계획 기한은 (M.DD.) 형식으로 쓸 것 - 예: (6.15.)", shown)
            if _SENTENCE.search(core):
                add("must", "서술형 문장으로 끝남 - 개조식(명사형 종결)으로 쓸 것", shown)
            n = len(core)
            if n > TWO_LINES[1]:
                add("must" if level != "star" else "should", f"{n}자 - 두 줄({TWO_LINES[1]}자)을 넘음. 줄이거나 꼭지를 나눌 것", shown)
            elif level != "star" and LINE < n < TWO_LINES[0]:
                add("should", f"{n}자 - 한 줄({LINE}자 이하)로 줄이거나 두 줄({TWO_LINES[0]}~{TWO_LINES[1]}자)로 채울 것", shown)
            if transcript and (missing := sorted(d for d in _digits(body) if len(d) >= 2 and d not in spoken)):
                add("check", f"녹취록에서 찾지 못한 숫자 {', '.join(missing)} - 말로 받아 적혔을 수 있으니 확인(근거가 없으면 지울 것)", shown)
            if level != "star":
                marked.append(core)
    nominal = sum(bool(_NOMINAL.search(body)) for body in marked)
    if len(marked) >= 4 and nominal * 2 > len(marked):
        add("must", f"○·- {len(marked)}줄 중 {nominal}줄이 -함/-음/-임으로 끝남 - 명사형 종결을 우선할 것(예: \"비용을 처리함\" → \"비용 처리\")")
    return issues


def _clean(text: str) -> str:
    """모델이 붙인 코드 울타리와 앞뒤 빈 줄을 걷어 낸다."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines() if not line.strip().startswith("```")).strip()


def _score(issues: list[dict]) -> tuple[int, int]:
    """(지침 위반 수, 권장 벗어남 수) - 작을수록 지침에 가깝다. 사람이 확인할 것(check)은 세지 않는다."""
    return sum(i["level"] == "must" for i in issues), sum(i["level"] == "should" for i in issues)


async def write(llm: LLMProvider, model: str, guide: str, transcript: str, pages: int, today: str, note=None) -> dict:
    """초안을 쓰고, 재고, 고쳐 쓴다. guide = 프롬프트 관리의 작성 지침(meeting_minutes) - 형식(LAYOUT)은 그 뒤에 항상 붙는다."""

    async def say(messages: list[ChatMessage], max_tokens: int = 3000) -> str:
        return _clean((await llm.chat(messages, temperature=0.2, model=model, max_tokens=max_tokens)).content or "")

    async def tell(message: str) -> None:
        if note:
            await note(message)

    plain = _STAMP.sub("", transcript).strip()
    system = ChatMessage(role="system", content=f"{guide.strip()}\n\n## 이번 보고서 분량\n{VOLUME[pages]}\n\n{LAYOUT}")
    ask = ChatMessage(role="user", content=(
        f"오늘 날짜: {today} (녹취에 날짜가 없으면 이 날짜를 씁니다)\n\n회의 녹취록:\n'''\n{plain}\n'''\n\n"
        f"위 녹취록으로 지침에 맞는 {pages}쪽 분량 회의 결과 보고서를 작성하세요."))

    await tell("초안을 쓰는 중")
    text = await say([system, ask])
    issues = lint(text, plain, pages, today)
    await tell("녹취록과 대조해 검토하는 중")
    review = await say([ChatMessage(role="system", content=REVIEW),
                        ChatMessage(role="user", content=f"녹취록:\n'''\n{plain}\n'''\n\n초안:\n'''\n{text}\n'''")], max_tokens=800)
    review = "" if review.replace(".", "").strip() == "없음" else review
    revisions = 0
    for round_ in range(MAX_REVISIONS):
        # 잰 것 중 고칠 것이 남았으면 다시 쓴다. 첫 번은 확인할 것(check)·검토 의견도 같이 준다.
        if not (any(_score(issues)) or (round_ == 0 and (issues or review))):
            break
        await tell(f"지침에 맞게 고쳐 쓰는 중 ({round_ + 1}/{MAX_REVISIONS})")
        found = "\n".join(f"- {i['message']}" + (f" → {i['line']}" if i["line"] else "") for i in issues)
        feedback = ("초안을 점검했습니다. 아래를 고친 보고서 전체를 같은 출력 형식으로 다시 쓰세요. "
                    "지적하지 않은 줄은 그대로 두고, 녹취록에 없는 내용은 넣지 마세요.\n"
                    + (f"\n[지침 점검 - 글자 수는 띄어쓰기 포함]\n{found}\n" if found else "")
                    + (f"\n[녹취록 대조 검토]\n{review}\n" if review and round_ == 0 else ""))
        revised = await say([system, ask, ChatMessage(role="assistant", content=text), ChatMessage(role="user", content=feedback)])
        again = lint(revised, plain, pages, today)
        # 고쳐 쓴 것이 지침에서 더 멀어졌으면 버린다(제목·구획을 잃은 글 포함).
        if revised and _score(again) <= _score(issues):
            text, issues, revisions = revised, again, revisions + 1
        else:
            break
    return {"text": text, "issues": issues, "revisions": revisions, "model": model}
