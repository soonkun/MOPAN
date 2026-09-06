"""행 묶음 청킹 - 표 파일(xlsx·csv) 전용.

산문 전략을 표에 적용하면 두 가지가 어긋난다(실측: 등록농약 10,001행 xlsx를
korean_legal 계층 전략이 잘라 행이 중간에서 끊기고 겹침 창이 임베딩을 1.7배로
부풀렸다 - 원문 2.8M 토큰이 4.9M 토큰으로):
  1) 행이 중간에서 잘리면 "상표명: 사파이어 | … | 희석배수: 2000배"라는 한
     사실이 두 청크로 갈라져, 표 조회 MCP의 정확 부분일치도 RAG 인용도 반쪽
     행을 든다.
  2) 겹침은 산문에서 문맥 절단을 무마하는 장치인데, 행은 서로 독립이라 겹침이
     무마할 절단이 없다 - 순수한 임베딩 낭비다.

그래서 여기서는 행(블록) 경계를 절대 자르지 않고, max_chunk_tokens까지 행을
채워 한 청크로 묶는다. 겹침 0. 시트가 바뀌면 끊는다 - 청크의 section이 곧
화면과 MCP 폴백의 좌표라서, 두 시트의 행이 한 좌표를 나눠 쓰면 안 된다.

get_chunking_strategy의 "NOTHING IS SNIFFED" 원칙과 충돌하지 않는다: 그 원칙이
금지한 것은 내용 휴리스틱(한국어 분류표 정규식 줄 세기)이었다. xlsx·csv는
형식 자체가 행 구조를 보증하므로, 파일 형식에 따른 이 선택은 냄새 맡기가
아니라 구성상의 사실이다. 선택은 worker(app/worker.py)가 document.file_type을
보고 한다.
"""

from anyio import to_thread

from app.core.tokens import count_tokens
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.structure import split_to_token_limit


class RowBundleChunking(ChunkingStrategy):
    def __init__(self, max_chunk_tokens: int = 1300):
        if max_chunk_tokens < 1:
            raise ValueError("max_chunk_tokens must be at least 1")
        self.max_chunk_tokens = max_chunk_tokens

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        # 임베딩하지 않는다(파이프라인이 일괄로 한다). tiktoken만 도는
        # CPU 작업이라 fixed.py와 같은 이유로 스레드에 태운다.
        return await to_thread.run_sync(self._chunk_sync, blocks)

    def _chunk_sync(self, blocks: list[Block]) -> list[ChunkCandidate]:
        candidates: list[ChunkCandidate] = []
        bundle: list[Block] = []
        bundle_tokens = 0

        def flush() -> None:
            nonlocal bundle, bundle_tokens
            if not bundle:
                return
            content = "\n".join(block.text for block in bundle)
            candidates.append(
                ChunkCandidate(
                    content=content,
                    # 정확값을 다시 센다: 합산치는 행 사이 개행 토큰을 모른다.
                    token_count=count_tokens(content),
                    char_count=len(content),
                    page=bundle[0].page,
                    section=bundle[0].section,
                    metadata={"strategy": "rows", "row_count": len(bundle)},
                )
            )
            bundle, bundle_tokens = [], 0

        for block in blocks:
            tokens = count_tokens(block.text)
            if tokens > self.max_chunk_tokens:
                # 한 행이 한도를 넘는 극단(셀 하나가 문단인 표). 행 경계 원칙은
                # 여기서만 진다 - 임베딩 입력 한도는 물리 법칙이라서. 산문
                # 전략들이 쓰는 같은 절단기로 자른다.
                flush()
                for part in split_to_token_limit(block.text, self.max_chunk_tokens):
                    candidates.append(
                        ChunkCandidate(
                            content=part,
                            token_count=count_tokens(part),
                            char_count=len(part),
                            page=block.page,
                            section=block.section,
                            metadata={"strategy": "rows", "row_count": 1},
                        )
                    )
                continue
            if bundle and (
                bundle_tokens + tokens > self.max_chunk_tokens
                or block.section != bundle[0].section
            ):
                flush()
            bundle.append(block)
            bundle_tokens += tokens
        flush()
        return candidates
