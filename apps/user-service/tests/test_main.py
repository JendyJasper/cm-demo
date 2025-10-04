from fastapi.testclient import TestClient
from user_service.main import app

client = TestClient(app)

def test_root():
    response = client.get("/")
    assert response.status_code == 200  # nosec B101
    body = response.json()
    assert body["service"] == "user-service"  # nosec B101
    assert body["status"] == "running"  # nosec B101

def test_health():
    response = client.get("/health")
    assert response.status_code == 200  # nosec B101
    assert response.json() == {"status": "healthy"}  # nosec B101