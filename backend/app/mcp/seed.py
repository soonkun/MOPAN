"""동봉 MCP의 자동 등록.

RAG 문서 표 조회 MCP(/tables/mcp)는 "문서를 임베딩하면 그 안의 표가 곧바로 조회
도구가 된다"는 성질이라 예시가 아니라 기본 기능이다. 그래서 관리자가 등록하는
대신 부팅이 등록한다: backend 기동 시(app/main.py lifespan)와 첫 관리자 계정이
만들어질 때(app/auth/service.py) 이 함수가 불리고, 두 곳 다 실패해도 앱은 뜬다 -
시딩은 다음 재기동이 다시 시도할 수 있는 종류의 일이고, 기동 거부는 아니다.

멱등 규칙: 같은 base_url의 행이 있으면 만들지 않고 builtin으로 승격만 한다
(이 배포처럼 관리자가 이미 손으로 등록한 경우). 도구는 행에 하나도 없을 때만
발견을 시도한다 - compose 부팅 경합으로 mcp-examples가 아직 안 떠 있으면
발견만 실패하고 행은 남아, 다음 재기동이 채운다. 관리자의 결정(enabled,
risk_level 재분류)은 건드리지 않는다.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import log_event
from app.mcp.service import discover
from app.models.mcp import McpServer, McpTool
from app.models.user import User

logger = logging.getLogger("mopan.mcp")

# 소유자가 고른 정확한 이름: RAG로 등록(임베딩)된 문서의 표를 읽는다는 사실이
# 이름에 그대로 있어야 한다. 이전 시드명은 아래 집합으로 따라 개명되지만,
# 관리자가 직접 지은 이름은 건드리지 않는다.
BUNDLED_SERVER_NAME = "RAG 문서 표 조회"
_PRIOR_SEED_NAMES = {"표 조회", "상품분류 조회"}

# 경로 개명(2026-09-05): /goods/mcp -> /tables/mcp. 구주소로 등록된 행은 아래
# 시딩이 base_url을 새 주소로 따라 붙인다(서버 쪽은 구경로도 별칭으로 응답).
_CANONICAL_SUFFIX = "/tables/mcp"
_LEGACY_SUFFIX = "/goods/mcp"


async def seed_bundled_servers(db: AsyncSession, settings: Settings) -> None:
    """Never raises: 시딩 실패가 기동이나 회원가입을 죽이면 안 된다."""
    url = settings.bundled_mcp_seed_url
    if not url:
        return
    try:
        server = await db.scalar(select(McpServer).where(McpServer.base_url == url))
        if server is None and url.endswith(_CANONICAL_SUFFIX):
            legacy_url = url[: -len(_CANONICAL_SUFFIX)] + _LEGACY_SUFFIX
            server = await db.scalar(select(McpServer).where(McpServer.base_url == legacy_url))
            if server is not None:
                server.base_url = url
        if server is None:
            # created_by는 NOT NULL RESTRICT다(사람이 화면에서 등록한 것과 같은
            # 스키마를 쓴다). 가장 오래된 활성 관리자를 소유자로 적는다 - 아직
            # 아무도 없으면 첫 관리자 가입이 이 함수를 다시 부른다.
            admin_id = await db.scalar(
                select(User.id)
                .where(User.role == "admin", User.is_active.is_(True))
                .order_by(User.created_at)
                .limit(1)
            )
            if admin_id is None:
                return
            server = McpServer(
                name=BUNDLED_SERVER_NAME, base_url=url, created_by=admin_id, builtin=True
            )
            db.add(server)
            await db.commit()
            log_event(logger, "mcp_server_seeded", server_id=str(server.id), server=server.name)
        else:
            server.builtin = True
            if server.name in _PRIOR_SEED_NAMES:
                server.name = BUNDLED_SERVER_NAME
            # builtin 승격·구주소 이관·시드명 개명을 한 번에. 다 맞으면 no-op commit.
            await db.commit()

        tool_count = (
            await db.scalar(
                select(func.count()).select_from(McpTool).where(McpTool.server_id == server.id)
            )
        ) or 0
        if tool_count == 0:
            tools = await discover(db, server, settings=settings)
            # 이 서버는 우리가 작성한 조회 전용 SQL이라 read가 사실이고, read여야
            # 자동 숙고(app/mcp/auto.py)가 쓸 수 있다. 첫 발견 때 한 번만 - 이후는
            # 관리자의 재분류가 이긴다(discover는 기존 행의 risk를 안 건드린다).
            for tool in tools:
                tool.risk_level = "read"
            await db.commit()
    except Exception:
        await db.rollback()
        logger.warning("bundled MCP seeding failed; retried on next start", exc_info=True)
