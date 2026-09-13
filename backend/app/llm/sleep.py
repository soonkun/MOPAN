"""vLLM sleep 모드 - 유휴 시 GPU 메모리를 비우고 첫 요청에 되살린다.

소유자 요청(2026-09-13): "일정 시간 안 쓰면 GPU 점유를 내렸다가 누군가 쓸 때 올려라." 프로세스를
내렸다 올리면 26b는 4분 20초라(콜드 로드 실사고 09-09) 못 쓴다. vLLM의 sleep level 1은 가중치를
CPU RAM으로 내리고 KV 캐시를 버린다 - GPU는 비고, /wake_up은 RAM에서 되돌리므로 수 초다.
서버는 `--enable-sleep-mode`와 `VLLM_SERVER_DEV_MODE=1`로 떠야 /sleep·/wake_up·/is_sleeping이 있다
(scripts/start_vllm*.sh).

두 역할, 두 자리:
- **재우기**는 백엔드 한 프로세스만 한다(`Sleeper`, main.py lifespan). 판단은 우리 프로세스의
  요청 기록이 아니라 vLLM 자신의 /metrics(`request_success_total`, `num_requests_running`)로 -
  워커 8개·평가 스크립트처럼 다른 프로세스가 보내는 요청도 그 서버의 숫자에는 다 찍힌다.
- **깨우기**는 요청을 보내는 모든 프로세스가 한다(`ensure_awake`, OpenAIProvider가 부른다):
  /is_sleeping이 참이면 /wake_up을 부르고 돌아올 때까지 기다린다. 한 프로세스 안의 동시 요청은
  base_url별 락으로 한 번만 깨운다. 확인은 요청마다 GET 하나(로컬, 1ms) - 상태를 캐시하면
  재운 직후의 요청이 서버 오류로 죽는다.
"""

import asyncio
import logging
import re
import time

import httpx

from app.core.logging import log_event

logger = logging.getLogger("mopan.models")

_METRIC_RE = re.compile(r"^vllm:(request_success_total|num_requests_running)\{[^}]*\}\s+([0-9.eE+-]+)$", re.M)
WAKE_TIMEOUT_SECONDS = 180.0
_locks: dict[str, asyncio.Lock] = {}


def _root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def parse_activity(metrics_text: str) -> tuple[float, float]:
    """(끝난 요청 누계, 지금 도는 요청 수). 어느 쪽도 없으면 (0, 0)."""
    finished = running = 0.0
    for name, value in _METRIC_RE.findall(metrics_text):
        if name == "request_success_total":
            finished += float(value)
        else:
            running += float(value)
    return finished, running


async def is_sleeping(base_url: str, timeout: float = 5.0) -> bool | None:
    """True/False, 서버가 안 뜨거나 sleep 모드가 없으면 None."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{_root(base_url)}/is_sleeping")
            if r.status_code != 200:
                return None
            return bool(r.json().get("is_sleeping"))
    except Exception:
        return None


async def ensure_awake(base_url: str) -> None:
    """자고 있으면 깨우고 돌아올 때까지 기다린다. 못 깨워도 raise하지 않는다 - 그 뒤의 실제 요청이
    제 오류로 실패하는 편이 여기서 추측하는 것보다 낫다."""
    if await is_sleeping(base_url) is not True:
        return
    lock = _locks.setdefault(base_url, asyncio.Lock())
    async with lock:
        if await is_sleeping(base_url) is not True:
            return
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=WAKE_TIMEOUT_SECONDS) as client:
                (await client.post(f"{_root(base_url)}/wake_up")).raise_for_status()
        except Exception:
            logger.warning("vllm wake_up failed at %s", base_url, exc_info=True)
            return
        log_event(logger, "vllm_woke_up", base_url=base_url, ms=int((time.perf_counter() - started) * 1000))


class Sleeper:
    """base_url마다 마지막으로 활동이 보인 시각을 기억하고, idle_minutes를 넘기면 재운다."""

    def __init__(self, base_urls: list[str]) -> None:
        self.base_urls = [u for u in base_urls if u]
        self._last_finished: dict[str, float] = {}
        self._last_active_at: dict[str, float] = {u: time.monotonic() for u in self.base_urls}

    async def _activity(self, base_url: str) -> tuple[float, float] | None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{_root(base_url)}/metrics")
                r.raise_for_status()
                return parse_activity(r.text)
        except Exception:
            return None

    async def tick(self, idle_minutes: int, *, now: float | None = None) -> list[str]:
        """한 번 점검. 재운 base_url 목록을 돌려준다(로그·테스트용)."""
        now = time.monotonic() if now is None else now
        slept: list[str] = []
        if idle_minutes <= 0:
            return slept
        for base_url in self.base_urls:
            activity = await self._activity(base_url)
            if activity is None:
                continue
            finished, running = activity
            if running > 0 or finished != self._last_finished.get(base_url):
                self._last_finished[base_url] = finished
                self._last_active_at[base_url] = now
                continue
            if now - self._last_active_at.get(base_url, now) < idle_minutes * 60:
                continue
            if await is_sleeping(base_url) is not False:
                continue  # 이미 자거나, sleep 모드가 없는 서버
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    (await client.post(f"{_root(base_url)}/sleep", params={"level": "1"})).raise_for_status()
            except Exception:
                logger.warning("vllm sleep failed at %s", base_url, exc_info=True)
                continue
            log_event(logger, "vllm_put_to_sleep", base_url=base_url, idle_minutes=idle_minutes)
            slept.append(base_url)
        return slept

    async def run(self, idle_minutes_getter, interval_seconds: float = 60.0) -> None:
        """lifespan 태스크. idle_minutes는 매번 새로 읽는다 - 설정 화면에서 바꾸면 재시작 없이 반영."""
        while True:
            try:
                await self.tick(int(await idle_minutes_getter()))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("vllm sleeper tick failed", exc_info=True)
            await asyncio.sleep(interval_seconds)
