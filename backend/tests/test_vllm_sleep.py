"""vLLM sleep 모드(app/llm/sleep.py): 유휴 판단은 서버 metrics로, 깨우기는 요청 전에 한 번."""
import httpx
import pytest

from app.llm import sleep as sleep_module
from app.llm.sleep import Sleeper, ensure_awake, parse_activity


def test_parse_activity_sums_success_counters_and_running():
    text = (
        'vllm:request_success_total{engine="0",finished_reason="stop",model_name="m"} 178.0\n'
        'vllm:request_success_total{engine="0",finished_reason="length",model_name="m"} 5.0\n'
        'vllm:num_requests_running{engine="0",model_name="m"} 2.0\n'
        "other_metric 9\n"
    )
    assert parse_activity(text) == (183.0, 2.0)
    assert parse_activity("") == (0.0, 0.0)


class FakeServer:
    def __init__(self):
        self.finished = 10.0
        self.running = 0.0
        self.sleeping = False
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/metrics":
            return httpx.Response(200, text=(
                f'vllm:request_success_total{{engine="0",finished_reason="stop",model_name="m"}} {self.finished}\n'
                f'vllm:num_requests_running{{engine="0",model_name="m"}} {self.running}\n'
            ))
        if path == "/is_sleeping":
            return httpx.Response(200, json={"is_sleeping": self.sleeping})
        if path == "/sleep":
            self.sleeping = True
            return httpx.Response(200)
        if path == "/wake_up":
            self.sleeping = False
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    real = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(fake.handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(sleep_module.httpx, "AsyncClient", fake_client)
    return fake


async def test_sleeper_sleeps_only_after_idle_minutes_of_no_activity(server):
    s = Sleeper(["http://vllm:8001/v1"])
    assert await s.tick(30, now=0.0) == []            # 첫 관측: 활동 기준점
    assert await s.tick(30, now=29 * 60) == []        # 아직 30분 전
    assert await s.tick(30, now=31 * 60) == ["http://vllm:8001/v1"]
    assert server.sleeping is True
    assert await s.tick(30, now=60 * 60) == []        # 이미 자면 다시 안 부른다
    # 깨어난 뒤 요청이 오가면 기준점이 갱신된다
    server.sleeping = False
    server.finished = 11.0
    assert await s.tick(30, now=61 * 60) == []
    assert await s.tick(30, now=61 * 60 + 29 * 60) == []
    assert await s.tick(30, now=61 * 60 + 31 * 60) == ["http://vllm:8001/v1"]


async def test_sleeper_never_sleeps_a_busy_server_or_when_disabled(server):
    s = Sleeper(["http://vllm:8001/v1"])
    server.running = 1.0
    await s.tick(30, now=0.0)
    assert await s.tick(30, now=10 ** 6) == [] and server.sleeping is False
    server.running = 0.0
    assert await s.tick(0, now=10 ** 7) == []          # 0 = 항상 상주


async def test_ensure_awake_wakes_once_and_is_a_noop_when_awake(server):
    await ensure_awake("http://vllm:8001/v1")
    assert "/wake_up" not in server.calls
    server.sleeping = True
    await ensure_awake("http://vllm:8001/v1")
    assert server.sleeping is False and server.calls.count("/wake_up") == 1
