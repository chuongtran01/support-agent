from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


class FakeSession:
    async def execute(self, _statement):
        return None


async def fake_session():
    yield FakeSession()


def test_health_ok_without_database():
    app.dependency_overrides[get_session] = fake_session
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
