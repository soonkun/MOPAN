"""영상 내용 추출 프록시(app/video): 로그인 필수, 작업에 사용자 id가 owner로 붙는다."""

import httpx
import pytest_asyncio

from app.video import router as video


@pytest_asyncio.fixture
async def service(app, client, monkeypatch):
    """가짜 추출 서비스. 받은 요청을 기록하고, 그 서비스처럼 owner가 다르면 404로 답한다."""
    seen: list[httpx.Request] = []
    jobs = {"20261002-090000-000": "someone-else"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        owner = request.url.params.get("owner", "")
        if request.method == "POST":
            return httpx.Response(200, json={"id": "new", "status": "queued"})
        if request.url.path == "/api/jobs":
            return httpx.Response(200, json={"gpu": "휴지", "idle_minutes": 10, "jobs": []})
        job_id = request.url.path.split("/")[3]
        if jobs.get(job_id) != owner:
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, content=b"docx", headers={"content-type": "application/x-test"})

    monkeypatch.setattr(video, "transport", httpx.MockTransport(handler))
    app.state.settings = app.state.settings.model_copy(
        update={"video_extract_url": "http://video.test", "video_extract_token": "tok-from-service"}
    )
    await client.post("/api/auth/register", json={"email": "boss@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "boss@example.com", "password": "pw123456"})
    return seen


async def test_requires_login(client):
    assert (await client.get("/api/video/jobs")).status_code == 401


async def test_unconfigured_is_503_in_korean(client):
    await client.post("/api/auth/register", json={"email": "boss@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "boss@example.com", "password": "pw123456"})
    response = await client.get("/api/video/jobs")
    assert response.status_code == 503 and "설정" in response.json()["detail"]


async def test_jobs_carry_the_callers_id_as_owner(client, service):
    me = (await client.get("/api/auth/me")).json()["id"]
    assert (await client.get("/api/video/jobs")).json()["jobs"] == []
    await client.post("/api/video/jobs", json={"url": "https://youtu.be/abc", "force": False})
    listed, posted = service
    assert listed.url.params["owner"] == me
    assert listed.headers["x-owner-token"] == posted.headers["x-owner-token"] == "tok-from-service"
    sent = posted.content.replace(b": ", b":")
    assert b'"owner":"' + me.encode() + b'"' in sent
    assert b'"summary_chars":500' in sent  # 길이를 안 보내면 기본 500자
    assert b'"full":false' in sent  # 전체 내용은 켠 사람만
    too_long = await client.post("/api/video/jobs", json={"url": "https://youtu.be/abc", "summary_chars": 5000})
    assert too_long.status_code == 422 and len(service) == 2


async def test_someone_elses_job_is_404_and_bad_names_never_reach_the_service(client, service):
    for name in ("report.docx", "report.pdf", "report.md"):
        assert (await client.get(f"/api/video/jobs/20261002-090000-000/{name}")).status_code == 404
    before = len(service)
    assert (await client.get("/api/video/jobs/20261002-090000-000/job.json")).status_code == 422
    assert len(service) == before
