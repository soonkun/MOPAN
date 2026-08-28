import pytest
from sqlalchemy import text

from app.main import EMBEDDING_DIM_SQL


async def test_health_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_reports_ready_when_dependencies_work(client):
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


async def test_ready_rejects_embedding_dim_mismatch(app, client, db):
    """Only reachable because ready() reads app.state.settings; with the module
    global it ignored the fixture's Settings and this branch was untestable."""
    deployed = await db.scalar(text(EMBEDDING_DIM_SQL))
    if deployed is None:
        pytest.skip("chunks table does not exist until Task 3")

    app.state.settings = app.state.settings.model_copy(
        update={"embedding_dim": deployed + 1}
    )
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert "does not match" in response.json()["detail"]
