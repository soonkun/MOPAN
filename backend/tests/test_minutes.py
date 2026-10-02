"""음성 전사·회의록(app/minutes): 글 읽기·지침 점검·한글 문서·고쳐 쓰기·프록시."""

import io
import re
import zipfile

import httpx
import pytest_asyncio

from app.llm.base import ChatResult
from app.minutes import draft, hwpx
from app.video import router as video

GOOD = """스마트농업 플랫폼 2단계 구축 점검 회의
[개요]
○ 일시·장소 : 2026.10.02. 14:00 / 본관 2층 회의실
○ 참 석 자 : 김영수 과장, 이정민 연구관
○ 2단계 구축 일정과 서버 증설 예산 집행 현황 점검

[주요내용]
○ 2단계 구축 착수일을 11월 2일로 확정
 - 데이터 표준화 완료가 착수의 선행 조건
  * 표준 항목 128개 중 96개 완료, 잔여 32개
○ 서버 증설 예산 잔액으로 GPU 서버 2대 구매
  * 예산 3억 2천만 원 중 1억 9천만 원 집행(59%)

[향후계획]
○ 데이터 표준화 잔여 32개 항목 완료 (10.23.)
"""
SPOKEN = "11월 2일 128개 96개 32개 3억 2천만 1억 9천만 59 10월 23일 2026년 10월 2일 14시"


def test_parse_reads_the_layout_and_joins_wrapped_lines():
    m = draft.parse(GOOD.replace("서버 2대 구매", "서버\n2대 구매"))
    assert m.title == "스마트농업 플랫폼 2단계 구축 점검 회의"
    assert m.when == "2026.10.02. 14:00 / 본관 2층 회의실" and m.attendees == "김영수 과장, 이정민 연구관"
    assert [level for level, _ in m.sections["주요내용"]] == ["o", "dash", "star", "o", "star"]
    assert m.sections["주요내용"][3][1] == "서버 증설 예산 잔액으로 GPU 서버 2대 구매"
    assert m.sections["개요"] == [("o", "2단계 구축 일정과 서버 증설 예산 집행 현황 점검")]  # 일시·참석자는 꼭지가 아니다


def test_lint_passes_a_report_that_follows_the_guide():
    assert draft.lint(GOOD, SPOKEN, 1, "2026.10.03.") == []


def test_lint_measures_what_the_guide_counts():
    def messages(text, level="must", **kwargs):
        return " / ".join(i["message"] for i in draft.lint(text, **kwargs) if i["level"] == level)

    assert "서술형" in messages(GOOD.replace("11월 2일로 확정", "11월 2일로 확정했습니다"))
    assert "(M.DD.)" in messages(GOOD.replace("(10.23.)", "(10월 23일)"))
    assert "YYYY.MM.DD." in messages(GOOD.replace("2026.10.02. 14:00", "2026년 10월 2일"))
    assert "[주요내용]에 ○ 꼭지가 없음" in messages(GOOD.split("[주요내용]")[0] + "[향후계획]\n○ 표준화 완료 (10.23.)\n")
    assert "마크다운" in messages(GOOD.replace("○ 2단계 구축 착수일", "○ **2단계** 구축 착수일"))
    assert "두 줄(72자)을 넘음" in messages(GOOD.replace("11월 2일로 확정", "11월 2일로 확정하고 " + "세부 일정 조정 " * 9))
    assert "○가 4개" in messages(GOOD.replace("[향후계획]", "○ 개인정보 영향평가 실시\n○ 시범 서비스 범위 제한\n[향후계획]"))
    assert "-함/-음/-임" in messages(GOOD.replace("확정\n", "확정함\n").replace("선행 조건", "선행 조건임").replace("2대 구매", "2대 구매함"))
    # 권장을 벗어남(고쳐 쓰게 한다): 어중간한 길이
    assert "한 줄(37자 이하)" in messages(GOOD.replace("11월 2일로 확정", "11월 2일로 확정하고 세부 일정은 수행사와 협의"), "should")
    # 사람이 확인할 것: 녹취록에 없는 숫자. "11.02."와 "11월 2일"은 같은 숫자로 본다
    assert "찾지 못한 숫자 77" in messages(GOOD.replace("59%", "77%"), "check", transcript=SPOKEN)
    assert messages(GOOD.replace("착수일을 11월 2일로", "착수일을 11.02.로"), "check", transcript=SPOKEN) == ""
    assert messages(GOOD.replace("59%", "77%"), "check") == ""  # 녹취록 없이 점검하면 숫자 대조는 하지 않는다


def test_hwpx_carries_the_text_as_written():
    data = hwpx.build(draft.parse(GOOD), "2026.10.03.", "기술융합과")
    archive = zipfile.ZipFile(io.BytesIO(data))
    first = archive.infolist()[0]
    assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED and archive.testzip() is None
    texts = [t for t in re.findall(r"<hp:t>([^<]*)</hp:t>", archive.read(hwpx.SECTION).decode()) if t]
    assert not any("{{" in t for t in texts)
    assert texts[:2] == ["스마트농업 플랫폼 2단계 구축 점검 회의", "2026.10.03. 기술융합과"]
    body = texts[texts.index(" 주요내용") + 1 : texts.index(" 향후계획")]
    assert body == [
        "○ 2단계 구축 착수일을 11월 2일로 확정",
        "  - 데이터 표준화 완료가 착수의 선행 조건",
        "   * 표준 항목 128개 중 96개 완료, 잔여 32개",
        "○ 서버 증설 예산 잔액으로 GPU 서버 2대 구매",
        "   * 예산 3억 2천만 원 중 1억 9천만 원 집행(59%)",
    ]
    assert texts[texts.index(" 향후계획") + 1] == "○ 데이터 표준화 잔여 32개 항목 완료 (10.23.)"
    # 향후계획이 없으면 그 제목 문단도 빠진다
    no_plan = hwpx.build(draft.parse(GOOD.split("[향후계획]")[0]), "2026.10.03.")
    assert " 향후계획" not in zipfile.ZipFile(io.BytesIO(no_plan)).read(hwpx.SECTION).decode()


class ScriptedLLM:
    """부른 순서대로 정해진 답을 준다(초안 → 검토 → 고쳐 쓰기…)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResult(content=self.answers.pop(0), usage={}, model="test")


async def test_write_puts_the_guide_first_and_revises_until_the_lint_is_clean():
    bad = GOOD.replace("11월 2일로 확정", "11월 2일로 확정했습니다")
    llm = ScriptedLLM(bad, "- 개인정보 영향평가 일정(11.20.)이 빠짐", "```\n" + GOOD + "```")
    result = await draft.write(llm, "test", "내 지침", "[00:00] " + SPOKEN * 3, 1, "2026.10.03.")
    assert result["text"] == GOOD.strip() and result["issues"] == [] and result["revisions"] == 1
    system = llm.calls[0][0].content
    assert system.startswith("내 지침") and "출력 형식" in system and "○ 2~3개" in system  # 지침이 먼저, 형식·분량은 코드가 덧붙임
    assert "[00:00]" not in llm.calls[0][1].content  # 시각 표시는 떼고 준다
    feedback = llm.calls[2][-1].content
    assert "서술형" in feedback and "영향평가" in feedback  # 잰 것과 검토 의견이 둘 다 간다


async def test_write_keeps_the_draft_when_the_revision_is_worse():
    bad = GOOD.replace("11월 2일로 확정", "11월 2일로 확정했습니다")
    llm = ScriptedLLM(bad, "없음", "죄송합니다. 다시 작성하겠습니다.")
    result = await draft.write(llm, "test", "지침", SPOKEN * 3, 1, "2026.10.03.")
    assert result["text"] == bad.strip() and result["revisions"] == 0 and len(llm.calls) == 3


@pytest_asyncio.fixture
async def member(client):
    await client.post("/api/auth/register", json={"email": "boss@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "boss@example.com", "password": "pw123456"})
    return (await client.get("/api/auth/me")).json()["id"]


async def test_every_route_requires_login(client):
    assert (await client.post("/api/minutes/drafts", json={"transcript": "x" * 30})).status_code == 401
    assert (await client.post("/api/minutes/hwpx", json={"text": GOOD})).status_code == 401
    assert (await client.post("/api/minutes/check", json={"text": GOOD})).status_code == 401
    assert (await client.post("/api/minutes/transcriptions?filename=a.m4a", content=b"x")).status_code == 401


async def test_check_and_hwpx_use_the_text_on_screen(client, member):
    edited = GOOD.replace("11월 2일로 확정", "11월 9일로 변경")
    assert (await client.post("/api/minutes/check", json={"text": edited})).json() == {"issues": []}
    response = await client.post("/api/minutes/hwpx", json={"text": edited, "date": "2026.10.03.", "department": "기술융합과"})
    assert response.status_code == 200 and "filename*=UTF-8''" in response.headers["content-disposition"]
    assert "11월 9일로 변경" in zipfile.ZipFile(io.BytesIO(response.content)).read(hwpx.SECTION).decode()
    unreadable = await client.post("/api/minutes/hwpx", json={"text": "그냥 글"})
    assert unreadable.status_code == 400 and "형식" in unreadable.json()["detail"]


async def test_a_draft_is_written_in_the_background_and_only_its_owner_reads_it(app, client, member):
    from app.chat import memory

    app.state.llm_provider = ScriptedLLM(GOOD, "없음")
    started = (await client.post("/api/minutes/drafts", json={"transcript": SPOKEN * 3, "pages": 1})).json()
    assert started["status"] == "running"
    await memory.drain()
    done = (await client.get(f"/api/minutes/drafts/{started['id']}")).json()
    assert done["status"] == "done" and done["text"] == GOOD.strip() and done["issues"] == []
    await client.post("/api/auth/logout")
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "other@example.com", "password": "pw123456"})
    assert (await client.get(f"/api/minutes/drafts/{started['id']}")).status_code == 404


async def test_audio_goes_to_the_service_with_the_callers_id(app, client, member, monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "20261003-090000-000", "status": "queued", "bytes": len(request.read())})
        return httpx.Response(200, json={"id": "20261003-090000-000", "status": "done", "text": "[00:00] 안녕하세요"})

    monkeypatch.setattr(video, "transport", httpx.MockTransport(handler))
    app.state.settings = app.state.settings.model_copy(update={"video_extract_url": "http://video.test", "video_extract_token": "tok"})
    posted = await client.post("/api/minutes/transcriptions?filename=회의.m4a", content=b"audio-bytes")
    assert posted.json()["bytes"] == len(b"audio-bytes")
    assert seen[0].url.params["owner"] == member and seen[0].url.params["filename"] == "회의.m4a"
    assert seen[0].headers["x-owner-token"] == "tok"
    assert (await client.get("/api/minutes/transcriptions/20261003-090000-000")).json()["text"] == "[00:00] 안녕하세요"
    assert seen[1].url.params["owner"] == member
    assert (await client.get("/api/minutes/transcriptions/../../etc")).status_code in (404, 422)
