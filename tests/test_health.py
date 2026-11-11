from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    """Verifica que /health responde 200 con estado ok sin requerir consentimiento."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
