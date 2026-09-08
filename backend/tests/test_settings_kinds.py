"""런타임 설정의 str·bool 종류 - 의도 분류 모델과 게이트 토글이 화면에서 편집된다."""
import pytest

from app.core.settings_store import RUNTIME_SAFE_SETTINGS


def test_intent_settings_parse_and_reject():
    gate = RUNTIME_SAFE_SETTINGS["INTENT_GATE"]
    assert gate.parse("false") is False and gate.parse("True") is True
    with pytest.raises(ValueError):
        gate.parse("maybe")
    model = RUNTIME_SAFE_SETTINGS["INTENT_MODEL"]
    assert model.parse(" gpt-4o-mini ") == "gpt-4o-mini"
    assert model.parse("") == ""
