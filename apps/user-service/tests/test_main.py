from fastapi.testclient import TestClient
from user_service.main import app   # <-- fixed import

client = TestClient(app)

def test_root():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "user-service"
    assert body["status"] == "running"

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
