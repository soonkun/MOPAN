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


# ---------------------------------------------------------------------------
# KIPRIS 상표분류 MCP - 두 번째 MCP 서버, 같은 컨테이너의 /kipris/mcp.
#
# plus.kipris.or.kr의 상표 분류코드·유사군코드 이력 REST(TradeMarkClassification
# InfoService)를 감싼다. KIPRIS Plus는 가입·활용신청 후 발급되는 accessKey가
# 필요하고 오퍼레이션 명세도 로그인 뒤의 개발가이드에만 있다 - 그래서 이름을
# 추측해 굳힌 개별 도구 대신, 가이드의 오퍼레이션명을 그대로 받는 게이트웨이
# 한 개를 정직하게 둔다(껍데기 금지). accessKey는 MOPAN의 MCP 서버 등록 화면
# "인증 토큰"에 넣으면 Authorization 헤더로 이 서버에 오고, 여기서 KIPRIS의
# accessKey 파라미터로 전달된다 - .env를 만질 일이 없다.

import xml.etree.ElementTree as ET

KIPRIS_BASE = "http://plus.kipris.or.kr/openapi/rest/TradeMarkClassificationInfoService"

KIPRIS_TOOLS = [
    {
        "name": "kipris_trademark_classification",
        "description": (
            "KIPRIS Plus TradeMarkClassificationInfoService gateway. Korean trademark (NICE) "
            "classification codes and similar-group-code (유사군코드) designated-goods change "
            "history, per application. `operation` is the operation name from the KIPRIS Plus "
            "developer guide for this service; `params` are its query parameters, e.g. "
            "{\"applicationNumber\": \"4020200012345\"} or paging like docsStart/docsCount. "
            "Responses are KIPRIS XML rendered as readable text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "KIPRIS operation name, e.g. from the service guide",
                },
                "params": {
                    "type": "object",
                    "description": "Query parameters for the operation (accessKey는 자동 주입)",
                },
            },
            "required": ["operation"],
        },
    },
]

KIPRIS_KEY_GUIDE = (
    "KIPRIS accessKey가 없습니다. plus.kipris.or.kr에서 가입 후 이 API의 활용신청으로 "
    "발급받은 accessKey를, MOPAN의 MCP 서버 등록 화면 '인증 토큰' 칸에 넣어 주세요. "
    "(월 1,000회 무료)"
)


def _xml_to_text(xml_text: str, limit: int = 4000) -> str:
    """KIPRIS XML을 '태그: 값' 줄들로 - 모델이 읽을 표 형태의 최소 변환."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text[:limit]
    lines: list[str] = []
    for element in root.iter():
        text = (element.text or "").strip()
        if text and len(element) == 0:
            lines.append(f"{element.tag}: {text}")
    return ("\n".join(lines) or xml_text)[:limit]


async def kipris_gateway(arguments: dict, access_key: str | None) -> str:
    if not access_key:
        return KIPRIS_KEY_GUIDE
    operation = str(arguments.get("operation", "")).strip().strip("/")
    if not operation or not operation.replace("_", "").isalnum():
        raise ValueError("operation은 KIPRIS 개발가이드의 오퍼레이션 이름 한 단어여야 합니다.")
    params = arguments.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params는 객체여야 합니다.")
    query = {str(k): str(v) for k, v in params.items()}
    query["accessKey"] = access_key
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        response = await client.get(f"{KIPRIS_BASE}/{operation}", params=query)
    body = response.text
    if response.status_code != 200 or "code400" in str(response.url):
        return (
            f"KIPRIS가 요청을 거절했습니다 (HTTP {response.status_code}, {response.url}). "
            "오퍼레이션 이름과 accessKey를 확인해 주세요."
        )
    return _xml_to_text(body)


@app.post("/kipris/mcp")
async def kipris_mcp(request: Request) -> Response:
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
                "serverInfo": {"name": "mopan-kipris", "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": KIPRIS_TOOLS})
    if method == "tools/call":
        params = payload.get("params") or {}
        if params.get("name") != "kipris_trademark_classification":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"unknown tool: {params.get('name')}"},
                }
            )
        auth = request.headers.get("authorization", "")
        access_key = auth.removeprefix("Bearer ").strip() or None
        try:
            text = await kipris_gateway(params.get("arguments") or {}, access_key)
            is_error = False
        except Exception as exc:
            logger.warning("kipris tool failed: %s", exc)
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


# ---------------------------------------------------------------------------
# 표 조회 MCP - 세 번째 서버, /goods/mcp. 키가 필요 없다.
#
# "상표분류 코드가 뭐냐" 같은 물음에 답하는 실체는 외부 API가 아니라 이미
# 코퍼스에 있는 표다(예: 유사상품 심사기준의 9천여 행 - 문서가 곧 데이터라는
# 모판 철학). 이 도구는 그 행들을 정확 부분일치로 조회해 [코드/코드] 마커를
# 결정적으로 돌려준다. 애매한 물음("소셜네트워크 어플")은 모델이 후보 명칭
# 여러 개로 바꿔 여러 번 조회해 종합한다 - 그 조합이 자동 사용 숙고의 일이다.
#
# 특정 표의 하드코딩이 아니다. 두 단계다:
#   1) "[코드/코드] 명칭…" 브래킷 마커 절(그 구조로 청킹된 문서의 산출물)을
#      먼저 찾는다 - 마커가 곧 코드라 결과가 행 단위로 결정적이다.
#   2) 마커 일치가 없으면 코퍼스 전체 본문을 같은 정확 부분일치로 조회한다 -
#      섹션 패턴을 설정하지 않고 문서만 올린 배포에서도 표의 행이 텍스트로
#      파싱되어 있는 한 잡힌다. 모판을 받은 사람이 아무 설정 없이 RAG 문서만
#      등록해도 이 도구가 빈 손이 되지 않는 것이 이 폴백의 존재 이유다.
# 상품분류 전용이 아니라서 이름도 "표 조회"다.
#
# 경로가 /goods/mcp인 것은 역사다: 이미 등록된 서버의 base_url이 이 주소라
# 경로를 바꾸면 기존 등록 행이 죽는다. 이름은 도구·서버명이 밖에 보이는
# 전부이고, 그 둘은 일반화했다.

import os

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine

_goods_engine = None


def _engine():
    global _goods_engine
    if _goods_engine is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL이 설정되지 않았습니다(compose의 mcp-examples 참조).")
        _goods_engine = create_async_engine(url, pool_size=2, pool_pre_ping=True)
    return _goods_engine


TABLE_TOOLS = [
    {
        "name": "table_lookup",
        "description": (
            "Exact-substring lookup over EVERY document indexed in this deployment - any table "
            "or text is searchable the moment it is indexed, no configuration needed. Rows from "
            "documents chunked with [code/code]-style section markers come back first as "
            "deterministic [class/code] rows (e.g. 유사상품 심사기준: goods name -> 상품류/"
            "유사군코드); when no marker row matches, the same keyword is searched across all "
            "chunk text and returned with its surrounding line, so the answer model reads the "
            "code or value straight out of the row. For a vague item, call several times with "
            "candidate official-style keywords (e.g. '소셜네트워크', '애플리케이션 소프트웨어') "
            "and combine."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "표에서 찾을 명칭/키워드 (부분일치, 2자 이상)",
                },
                "limit": {"type": "integer", "description": "최대 결과 수 (기본 8, 최대 20)"},
            },
            "required": ["keyword"],
        },
    },
]

_TABLE_SQL = sql_text(
    r"""
    SELECT DISTINCT ON (marker) marker, content, filename FROM (
      SELECT split_part(c.section, ']', 1) || ']' AS marker, c.content, d.filename
      FROM chunks c JOIN documents d ON d.id = c.document_id
      WHERE c.section ~ '^\[[^]]+/[^]]+\]' AND c.content ILIKE '%' || :q || '%'
    ) rows ORDER BY marker LIMIT :n
    """
)

# 폴백: 마커 구조가 없는 배포를 위한 전(全) 코퍼스 정확 부분일치. 키 컬럼을
# 추측하지 않는다 - 일치한 행의 주변 텍스트를 그대로 돌려주고, 어떤 값이
# 코드인지는 답변 모델이 문맥으로 읽는다. 섹션이 있으면 섹션을, 없으면
# 파일명을 좌표로 붙인다.
_FALLBACK_SQL = sql_text(
    """
    SELECT COALESCE(NULLIF(c.section, ''), d.filename) AS place, c.content, d.filename
    FROM chunks c JOIN documents d ON d.id = c.document_id
    WHERE c.content ILIKE '%' || :q || '%'
    ORDER BY d.filename, c.chunk_index LIMIT :n
    """
)


async def table_lookup(arguments: dict) -> str:
    # 구 스키마의 "name"도 받는다: 등록된 도구 설명이 재발견 전까지 옛 것일 수 있다.
    keyword = str(arguments.get("keyword") or arguments.get("name") or "").strip()
    if len(keyword) < 2:
        raise ValueError("keyword는 2자 이상의 명칭/키워드여야 합니다.")
    limit = min(max(int(arguments.get("limit") or 8), 1), 20)
    async with _engine().connect() as conn:
        rows = (await conn.execute(_TABLE_SQL, {"q": keyword, "n": limit})).all()
        structured = bool(rows)
        if not rows:
            rows = (await conn.execute(_FALLBACK_SQL, {"q": keyword, "n": limit})).all()
    if not rows:
        return (
            f"'{keyword}'과 일치하는 텍스트가 코퍼스에 없습니다. 더 공식적인 명칭"
            f"(예: '~업', '~용 소프트웨어')이나 다른 표현으로 다시 조회해 보세요."
        )
    head = (
        f"'{keyword}' 일치 {len(rows)}건 (마커 = [코드/코드]):"
        if structured
        else f"'{keyword}' 마커 구조 일치 없음 - 전체 본문 정확일치 {len(rows)}건:"
    )
    lines = [head]
    for place, content, filename in rows:
        at = content.lower().find(keyword.lower())
        start = max(0, at - 40)
        window = content[start : at + len(keyword) + 60].replace("\n", " ")
        lines.append(f"- {place} …{window}… ({filename})")
    return "\n".join(lines)


@app.post("/goods/mcp")
async def goods_mcp(request: Request) -> Response:
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
                "serverInfo": {"name": "mopan-tables", "version": "1.1.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": TABLE_TOOLS})
    if method == "tools/call":
        params = payload.get("params") or {}
        # goods_classification은 구명이다: 재발견 전의 등록 행이 아직 그 이름으로
        # 부를 수 있어 별칭으로 받는다.
        if params.get("name") not in ("table_lookup", "goods_classification"):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"unknown tool: {params.get('name')}"},
                }
            )
        try:
            text = await table_lookup(params.get("arguments") or {})
            is_error = False
        except Exception as exc:
            logger.warning("table_lookup failed: %s", exc)
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
