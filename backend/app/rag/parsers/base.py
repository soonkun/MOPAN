import re
from abc import ABC, abstractmethod

from app.rag.blocks import ParsedDocument


class ParseFailure(Exception):
    """사용자에게 그대로 보여도 되는 한국어 메시지를 담은 파싱 실패.

    일반 예외는 파이프라인이 정체를 숨긴 채 "문서를 처리하지 못했습니다"로
    뭉뚱그리지만, 원인이 파일 자체에 있고 사용자가 고칠 수 있을 때는 그
    사실을 말해 줘야 한다 - (cid:NNNN) 3천 청크가 조용히 색인되어 검색을
    오염시키던 실사고에서 왔다."""


class Parser(ABC):
    """Synchronous by design: parsing is CPU-bound. Callers must run it through
    anyio.to_thread so it never blocks the API or worker event loop."""

    @abstractmethod
    def parse(self, path: str, section_marker: re.Pattern[str] | None = None) -> ParsedDocument:
        """`section_marker` is the collection's configured section-header pattern,
        the same one the chunking layer cuts on. A line matching it is a heading
        BY CONSTRUCTION rather than by heuristic, so a parser that can tell lines
        apart should say so; one that cannot is free to ignore it.

        It is threaded rather than hardcoded because the pattern is per-collection
        configuration - see app/rag/chunking/table.py. It used to be a Korean
        goods-classification regex living in app/rag/blocks.py, which no other
        user of this product could have benefited from.
        """
