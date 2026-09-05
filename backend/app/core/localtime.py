"""사용자의 '지금'을 모델에게 알려주는 한 줄.

"올해 휴일 알려줘"가 2023년 공휴일로 답하던 실사고에서 왔다: 모델의 "올해"는
학습 시점에 고정되어 있고, 그것을 끊는 유일한 방법은 요청마다 현재 날짜를
프롬프트에 싣는 것이다. 시간대는 브라우저가 보낸 값(사용자의 시스템 설정 -
위치정보의 실체)이 먼저고, 없거나 이상하면 배포 기본값으로 강등된다.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

_WEEKDAYS = "월화수목금토일"


def now_line(client_tz: str | None, fallback_tz: str) -> str:
    """숙고·답변 프롬프트에 덧붙는 문장. 어떤 실패든 fallback 시간대로 강등."""
    name = client_tz or fallback_tz
    try:
        tz = ZoneInfo(name)
    except Exception:
        name = fallback_tz
        tz = ZoneInfo(fallback_tz)
    now = datetime.now(tz)
    return (
        f"Current local date/time for the user: {now:%Y-%m-%d}({_WEEKDAYS[now.weekday()]}) "
        f"{now:%H:%M}, timezone {name}. Resolve every relative date - 올해, 이번 달, 내일, "
        f"지난주 - against this, never against your training data."
    )
