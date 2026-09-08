"""내장 표 조회의 근거는 문서 검색을 막지 않는다 - 부분일치 소음이 근거로 둔갑한 실사고."""
from app.mcp.service import substantive, to_evidence


def test_builtin_evidence_is_substantive_but_not_external():
    builtin = to_evidence("RAG 문서 표 조회", "table_lookup", "'어플' 일치 4건", {"builtin": True})
    weather = to_evidence("생활정보", "weather", "서울 맑음 24도")
    kept = substantive([builtin, weather])
    assert kept == [builtin, weather]
    external = [e for e in kept if not e.metadata.get("builtin")]
    assert external == [weather]
