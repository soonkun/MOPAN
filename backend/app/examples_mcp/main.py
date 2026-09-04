"""생활정보 MCP 서버 - 동봉 예시이자 실물.

"실제로 도움이 되는 MCP를 가져와봐"에 대한 답이다. 인증 없이 쓸 수 있는 공개
원격 MCP는 드물고(법령정보 MCP는 law.go.kr 계정이 필요하다), 그래서 키가 필요
없는 공공 API 세 가지를 MCP 도구로 감싸 함께 배포한다:

- current_weather: open-meteo (지오코딩 + 예보, 무료·무키)
- exchange_rate:   frankfurter.app (유럽중앙은행 고시 환율, 무료·무키)
- korean_holidays: date.nager.at (공휴일, 무료·무키)

프로토콜은 이 저장소의 클라이언트(app/mcp/client.py)가 말하는 것과 같은
streamable HTTP JSON-RPC다: initialize -> notifications/initialized ->
tools/list -> tools/call. 상태는 없고, 세션도 없다 - 요청 하나가 전부다.

backend 이미지로 그대로 돌므로(fastapi·httpx·uvicorn 재사용) 의존성도
Dockerfile도 새로 없다. DB·Redis에는 손대지 않는다.
"""

import logging
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger("mopan.examples_mcp")

app = FastAPI(title="MOPAN 생활정보 MCP", docs_url=None, redoc_url=None)

PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = httpx.Timeout(10.0)  # follow_redirects: frankfurter.app -> api.frankfurter.dev 301 (실측)

# WMO weather interpretation codes - open-meteo가 쓰는 표준. 주요 코드만 옮기고
# 나머지는 코드 숫자를 그대로 보여준다(없는 번역을 지어내지 않는다).
WMO = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "가랑비(약)", 53: "가랑비", 55: "가랑비(강)",
    61: "비(약)", 63: "비", 65: "비(강)",
    66: "어는 비(약)", 67: "어는 비(강)",
    71: "눈(약)", 73: "눈", 75: "눈(강)", 77: "싸락눈",
    80: "소나기(약)", 81: "소나기", 82: "소나기(강)",
    85: "소낙눈(약)", 86: "소낙눈(강)",
    95: "뇌우", 96: "뇌우(우박 동반)", 99: "뇌우(강한 우박)",
}

TOOLS = [
    {
        "name": "current_weather",
        "description": (
            "Current weather and a 3-day outlook for a city, from open-meteo. "
            "Use for any question about weather, temperature, rain or snow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, e.g. 서울, Seoul, Tokyo",
                }
            },
            "required": ["city"],
        },
    },
    {
        "name": "exchange_rate",
        "description": (
            "Latest foreign exchange rate between two currencies (ECB reference "
            "rates via frankfurter.app). Use for currency conversion questions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_currency": {"type": "string", "description": "ISO code, e.g. USD"},
                "to_currency": {"type": "string", "description": "ISO code, e.g. KRW"},
                "amount": {"type": "number", "description": "Amount to convert (default 1)"},
            },
            "required": ["from_currency", "to_currency"],
        },
    },
    {
        "name": "korean_holidays",
        "description": (
            "Public holidays in South Korea for a given year, from date.nager.at. "
            "Use for questions about 공휴일, 연휴 or day-off planning."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"year": {"type": "integer", "description": "Year, e.g. 2026"}},
            "required": ["year"],
        },
    },
]


async def _get_json(client: httpx.AsyncClient, url: str, **params: Any) -> Any:
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


# open-meteo 지오코딩은 한글 표기를 검색 색인에 넣지 않는다(실측: '서울' 0건,
# 'Seoul' 1건 - 응답의 name은 '서울특별시'로 오는데도). 주요 도시는 별칭으로
# 건너고, 그 밖은 행정 접미사를 떼고 재시도한다.
# ponytail: 별칭 표는 광역시급까지 - 시·군 단위가 필요해지면 로마자 변환기로.
KO_CITY = {
    "서울": "Seoul", "부산": "Busan", "인천": "Incheon", "대구": "Daegu",
    "대전": "Daejeon", "광주": "Gwangju", "울산": "Ulsan", "세종": "Sejong",
    "수원": "Suwon", "고양": "Goyang", "용인": "Yongin", "창원": "Changwon",
    "성남": "Seongnam", "청주": "Cheongju", "전주": "Jeonju", "천안": "Cheonan",
    "포항": "Pohang", "제주": "Jeju", "춘천": "Chuncheon", "강릉": "Gangneung",
    "평양": "Pyongyang",
}
_SUFFIXES = ("특별자치시", "특별자치도", "특별시", "광역시", "시", "군", "구")


def _city_candidates(city: str) -> list[str]:
    stripped = city
    for suffix in _SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) > len(suffix):
            stripped = stripped[: -len(suffix)]
            break
    names = [city]
    for key in (city, stripped):
        if key in KO_CITY:
            names.append(KO_CITY[key])
    if stripped != city:
        names.append(stripped)
    return list(dict.fromkeys(names))


async def current_weather(arguments: dict) -> str:
    city = str(arguments.get("city", "")).strip()
    if not city:
        raise ValueError("city가 비어 있습니다.")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        places = []
        for candidate in _city_candidates(city):
            geo = await _get_json(
                client,
                "https://geocoding-api.open-meteo.com/v1/search",
                name=candidate, count=1, language="ko", format="json",
            )
            places = geo.get("results") or []
            if places:
                break
        if not places:
            return f"'{city}'라는 도시를 찾지 못했습니다. 도시 이름을 확인해 주세요."
        place = places[0]
        forecast = await _get_json(
            client,
            "https://api.open-meteo.com/v1/forecast",
            latitude=place["latitude"], longitude=place["longitude"],
            current="temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            timezone="auto", forecast_days=3,
        )
    now = forecast["current"]
    code = int(now.get("weather_code", -1))
    where = place["name"] + (f" ({place.get('country', '')})" if place.get("country") else "")
    lines = [
        f"{where} 현재 날씨: {WMO.get(code, f'코드 {code}')}",
        f"기온 {now['temperature_2m']}°C (체감 {now['apparent_temperature']}°C), "
        f"습도 {now['relative_humidity_2m']}%, 바람 {now['wind_speed_10m']}km/h, "
        f"강수 {now['precipitation']}mm",
        "",
        "3일 전망:",
    ]
    daily = forecast["daily"]
    for i, day in enumerate(daily["time"]):
        day_code = int(daily["weather_code"][i])
        lines.append(
            f"- {day}: {WMO.get(day_code, f'코드 {day_code}')}, "
            f"{daily['temperature_2m_min'][i]}~{daily['temperature_2m_max'][i]}°C, "
            f"강수확률 {daily['precipitation_probability_max'][i]}%"
        )
    return "\n".join(lines)


async def exchange_rate(arguments: dict) -> str:
    src = str(arguments.get("from_currency", "")).strip().upper()
    dst = str(arguments.get("to_currency", "")).strip().upper()
    amount = float(arguments.get("amount") or 1)
    if not src or not dst:
        raise ValueError("from_currency와 to_currency가 필요합니다.")
    if src == dst:
        return f"{src}와 {dst}는 같은 통화입니다."
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        data = await _get_json(
            client, "https://api.frankfurter.app/latest", base=src, symbols=dst, amount=amount
        )
    rate = data.get("rates", {}).get(dst)
    if rate is None:
        return f"{src}->{dst} 환율을 제공하지 않습니다. ISO 통화 코드를 확인해 주세요."
    return f"{data['date']} 기준(ECB 고시): {amount:g} {src} = {rate:,} {dst}"


async def korean_holidays(arguments: dict) -> str:
    year = int(arguments.get("year") or 0)
    if not 1975 <= year <= 2100:
        raise ValueError("year는 1975~2100 사이의 연도여야 합니다.")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        days = await _get_json(client, f"https://date.nager.at/api/v3/PublicHolidays/{year}/KR")
    lines = [f"{year}년 대한민국 공휴일 ({len(days)}일):"]
    lines += [f"- {d['date']}: {d.get('localName') or d['name']}" for d in days]
    return "\n".join(lines)


HANDLERS = {
    "current_weather": current_weather,
    "exchange_rate": exchange_rate,
    "korean_holidays": korean_holidays,
}


def _result(request_id: Any, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/mcp")
async def mcp(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
            status_code=400,
        )
    method = payload.get("method")
    request_id = payload.get("id")

    if method == "notifications/initialized":
        return Response(status_code=202)
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mopan-life", "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"unknown tool: {name}"},
                }
            )
        try:
            text = await handler(params.get("arguments") or {})
            is_error = False
        except Exception as exc:  # 업스트림 API 죽음·잘못된 인자 - 도구 결과로 강등
            logger.warning("tool %s failed: %s", name, exc)
            text = f"도구 실행에 실패했습니다: {exc}"
            is_error = True
        return _result(
            request_id, {"content": [{"type": "text", "text": text}], "isError": is_error}
        )
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"unknown method: {method}"},
        }
    )
