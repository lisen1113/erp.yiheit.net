from fastapi.testclient import TestClient
from yihe_api.main import app


def test_missing_database_does_not_affect_liveness(monkeypatch):
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["postgres"] == "unavailable"
    assert response.headers["cache-control"] == "no-store"
