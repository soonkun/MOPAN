"""뉴스 브리핑 - 주제·검색어를 구독하면 날마다 전날 기사를 모아 브리핑(PDF·한글·Markdown)을 만들고 메일로 보내는 화면의 백엔드.

일은 soonkun/news-briefing 서비스(NEWS_BRIEFING_URL)가 한다(네이버 뉴스 수집, GPU로 분류·묶기·요약, 낱말 구름, 문서).
여기는 app/video처럼 로그인한 사용자의 요청에 사용자 id를 owner로 붙여 넘기는 얇은 문이다.
메일 받는 주소는 누구나 정한다(소유자 결정 2026-10-03: "사용자도 받고 싶은 메일 주소가 있을 텐데"). 가입이 열려 있어 임의 주소로
날마다 메일을 보내게 하는 통로가 될 수는 있다 - 그 서비스가 한 사람당 구독 10개·주소 하나로 묶고, 받는 주소는 구독 목록에 그대로 보인다.
"""
from fastapi import APIRouter, Depends, Path
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.core.config import Settings, get_app_settings
from app.models.user import User
from app.video.router import _call

router = APIRouter(prefix="/api/briefing", tags=["briefing"])
ID = r"^[0-9a-f-]{1,40}$"


async def _news(settings: Settings, method: str, path: str, **kwargs):
    return await _call(settings, method, path, base=settings.news_briefing_url, token=settings.news_briefing_token,
                       service="뉴스 브리핑", **kwargs)


class Window(BaseModel):
    """회차 하나(조간·석간처럼 따로 가는 설정): start~end에 나온 기사를 run 시각에 모아 만들고 deliver 시각에 보낸다(KST).
    start == end면 24시간. 자정을 넘는 범위(16:00→09:00)도 된다. deliver가 비면 만들어지는 대로 보낸다."""
    label: str = Field(default="", max_length=20)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    run: str = Field(pattern=r"^\d{2}:\d{2}$")
    deliver: str = Field(default="", pattern=r"^(\d{2}:\d{2})?$")


class Entity(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    group: str = Field(default="", max_length=30)
    note: str = Field(default="", max_length=60)
    role: str = Field(default="", max_length=20)


class NewSubscription(BaseModel):
    name: str = Field(min_length=1, max_length=40)       # 브리핑 제목이 되는 주제
    style: str = Field(default="topic", pattern=r"^(topic|tracker)$")  # 주제 브리핑 | 인물·기관 동향표
    queries: list[str] = Field(default=[], max_length=10)  # 주제: 검색어 / 동향표: '기타 보도' 키워드
    entities: list[Entity] = Field(default=[], max_length=40)  # 동향표: 추적 대상
    scope: str = Field(default="", max_length=60)        # 동향표: 관심 분야
    sites: list[str] = Field(default=[], max_length=5)   # 따로 볼 사이트(선택)
    mail: bool = True                                    # 메일 발송 스위치. 끄면 화면에만 쌓인다
    mail_to: str = Field(default="", max_length=200)     # 받는 주소. 비면 계정 메일
    formats: list[str] = Field(default=["pdf"], max_length=3)
    windows: list[Window] = Field(default=[Window(start="00:00", end="00:00", run="07:00")], min_length=1, max_length=3)
    keep_days: int = Field(default=10, ge=10, le=30)     # 결과물 보관 일수. 지나면 서비스가 지운다


class Run(BaseModel):
    date: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")  # 그날 하루
    window: int | None = Field(default=None, ge=0, le=2)  # 이 조사 창의 가장 최근 구간
    hours: int = Field(default=24, ge=1, le=168)          # 둘 다 없으면 지금부터 N시간 전까지
    send: bool = False


@router.get("/subscriptions")
async def list_all(user: User = Depends(get_current_user), settings: Settings = Depends(get_app_settings)) -> dict:
    data = (await _news(settings, "GET", "/api/subscriptions", params={"owner": str(user.id)})).json()
    return {**data, "account_email": user.email}


@router.post("/subscriptions")
async def add(body: NewSubscription, user: User = Depends(get_current_user), settings: Settings = Depends(get_app_settings)) -> dict:
    mail_to = (body.mail_to.strip() or (user.email or "")) if body.mail else ""
    payload = {**body.model_dump(exclude={"mail"}), "mail_to": mail_to, "owner": str(user.id)}
    return (await _news(settings, "POST", "/api/subscriptions", json=payload)).json()


@router.delete("/subscriptions/{sub_id}")
async def remove(sub_id: str = Path(pattern=ID), user: User = Depends(get_current_user),
                 settings: Settings = Depends(get_app_settings)) -> dict:
    return (await _news(settings, "DELETE", f"/api/subscriptions/{sub_id}", params={"owner": str(user.id)})).json()


@router.post("/subscriptions/{sub_id}/run")
async def run(body: Run, sub_id: str = Path(pattern=ID), user: User = Depends(get_current_user),
              settings: Settings = Depends(get_app_settings)) -> dict:
    send = body.send  # 받는 주소는 구독에 저장된 것(일반 사용자는 자기 계정 메일)이라 여기서 더 가릴 것이 없다
    return (await _news(settings, "POST", f"/api/subscriptions/{sub_id}/run",
                        json={"owner": str(user.id), "date": body.date, "window": body.window, "hours": body.hours, "send": send})).json()


@router.get("/reports/{report_id}/{name}")
async def download(
    report_id: str = Path(pattern=r"^[0-9-]{1,40}$"),
    name: str = Path(pattern=r"^report\.(pdf|hwpx|md|html)$"),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    upstream = await _news(settings, "GET", f"/api/reports/{report_id}/{name}", params={"owner": str(user.id)})
    headers = {k: upstream.headers[k] for k in ("content-disposition",) if k in upstream.headers}
    if name.endswith(".html"):
        # 기사 제목·요약은 밖에서 온 글이다. 그 서비스가 전부 이스케이프하지만, 우리 출처에서 열리는 화면이라 한 겹 더:
        # 스크립트·폼·바깥 자원을 막는다(그림은 문서 안에 든 낱말 구름뿐). allow-popups: 기사 링크를 새 창으로 열 수 있게.
        headers["Content-Security-Policy"] = "sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    return Response(content=upstream.content, media_type=upstream.headers.get("content-type", "application/octet-stream"), headers=headers)
