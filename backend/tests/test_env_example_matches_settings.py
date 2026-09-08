"""`.env.example`의 키 집합 == Settings 필드 집합.

2026-09-08 재배포에서 ANSWER_MODELS·QUERY_EXPANSION_RETRY_MODEL·INTENT_MODEL 등이 .env에만
추가되고 예시 파일은 그대로 GitHub에 남아, 새 서버가 기능을 잃었다. 설정을 추가하며
예시 파일을 안 고치면 여기서 실패한다. 값은 보지 않는다 - 키만 본다.
"""
import re
from pathlib import Path

from app.core.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
# 예시 파일에만 있어도 되는 키: 라이브러리·운영 환경이 읽고 Settings는 모르는 것.
# API_INTERNAL_URL은 프론트 빌드가, REDIS_PASSWORD는 compose의 redis가 읽는다.
NOT_SETTINGS = {
    "COMPOSE_PROJECT_NAME", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    "API_INTERNAL_URL", "REDIS_PASSWORD",
}
# Settings에 있지만 예시 파일에 굳이 두지 않는 것(있다면 여기 이유와 함께).
NOT_IN_EXAMPLE: set[str] = set()


def _example_keys() -> set[str]:
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#?\s*([A-Z][A-Z0-9_]+)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def test_env_example_lists_every_setting_and_nothing_unknown():
    fields = {name.upper() for name in Settings.model_fields}
    example = _example_keys()
    missing = fields - example - NOT_IN_EXAMPLE
    unknown = example - fields - NOT_SETTINGS
    assert not missing, f".env.example에 없는 설정: {sorted(missing)}"
    assert not unknown, f"Settings가 모르는 예시 키: {sorted(unknown)}"
