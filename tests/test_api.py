import pytest
from fastapi.testclient import TestClient

from app.agent.state import OutputType
from app.api.deps import llm_dep, store_dep
from app.core.limiter import limiter
from app.main import app
from app.rag.store import ProfileStore
from app.schemas.documents import JobAnalysis, TailoredCV
from tests.test_agent import ANALYSIS, FakeLLM, cv_with
from tests.test_rag import FakeEmbedder

JOB = "Buscamos desarrollador backend con Python y FastAPI para construir APIs REST." * 2
CV_MD = (
    b"# Experiencia\nDesarrollador Python con FastAPI creando una API REST.\n"
    b"# Skills\nPython FastAPI API Docker"
)


@pytest.fixture
def store():
    s = ProfileStore(path="", embedding_function=FakeEmbedder())
    yield s
    s.reset()


@pytest.fixture
def make_client(store, monkeypatch):
    monkeypatch.setattr("app.agent.graph.MIN_SCORE", 0.1)
    monkeypatch.setattr("app.agent.graph.MIN_TOP_SCORE", 0.0)
    limiter.enabled = False
    limiter.reset()

    def _make(llm=None):
        app.dependency_overrides[store_dep] = lambda: store
        app.dependency_overrides[llm_dep] = lambda: llm
        return TestClient(app, headers={"X-Privacy-Consent": "1"})

    yield _make
    app.dependency_overrides.clear()
    limiter.enabled = True


def test_ingest_list_and_delete(make_client):
    c = make_client()
    r = c.post(
        "/ingest",
        files={"file": ("cv.md", CV_MD)},
        data={"doc_type": "cv"},
    )
    assert r.status_code == 200
    assert r.json() == {"source": "cv.md", "doc_type": "cv", "chunks": 2}

    r = c.get("/sources")
    assert r.json() == {"sources": {"cv.md": 2}, "total_chunks": 2}

    assert c.delete("/sources/cv.md").status_code == 204
    assert c.delete("/sources/cv.md").status_code == 404


def test_ingest_rejects_unsupported_format(make_client):
    r = make_client().post("/ingest", files={"file": ("cv.docx", b"x")})
    assert r.status_code == 415


def test_ingest_rejects_large_file(make_client, monkeypatch):
    monkeypatch.setattr("app.api.profile.settings.max_upload_bytes", 10)
    r = make_client().post("/ingest", files={"file": ("cv.md", b"x" * 50)})
    assert r.status_code == 413


def test_ingest_path_traversal_name_is_sanitized(make_client):
    r = make_client().post("/ingest", files={"file": ("../../evil.md", CV_MD)})
    assert r.json()["source"] == "evil.md"


def test_generate_ok_returns_document_and_used_evidence(make_client):
    c = make_client(FakeLLM({"JobAnalysis": [ANALYSIS], "TailoredCV": [cv_with(["cv.md::0"])]}))
    c.post("/ingest", files={"file": ("cv.md", CV_MD)}, data={"doc_type": "cv"})
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document"]["headline"] == "Dev"
    assert [e["id"] for e in body["evidence"]] == ["cv.md::0"]
    assert body["attempts"] == 1
    assert "X-Request-ID" in r.headers


def test_generate_validates_input(make_client):
    c = make_client()
    assert c.post("/generate", json={"job_text": "corto", "output_type": "cv"}).status_code == 422
    assert c.post("/generate", json={"job_text": JOB, "output_type": "otro"}).status_code == 422


def test_generate_without_profile_is_422(make_client):
    c = make_client(FakeLLM({"JobAnalysis": [ANALYSIS]}))
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 422
    assert "evidencia" in r.json()["detail"]


def test_generate_failed_validation_is_502(make_client):
    llm = FakeLLM({"JobAnalysis": [ANALYSIS], "TailoredCV": [cv_with(["x::1"])] * 3})
    c = make_client(llm)
    c.post("/ingest", files={"file": ("cv.md", CV_MD)}, data={"doc_type": "cv"})
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 502


def test_upstream_llm_error_is_503(make_client):
    from langchain_google_genai._common import GoogleGenerativeAIError

    class BrokenLLM:
        def with_structured_output(self, *_a, **_k):
            raise GoogleGenerativeAIError("503 UNAVAILABLE")

    c = make_client(BrokenLLM())
    c.post("/ingest", files={"file": ("cv.md", CV_MD)}, data={"doc_type": "cv"})
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 503
    assert "saturado" in r.json()["detail"]


def test_requires_privacy_consent(make_client):
    c = make_client()
    c.headers.pop("X-Privacy-Consent")
    assert c.get("/sources").status_code == 403
    assert c.post("/ingest", files={"file": ("cv.md", CV_MD)}).status_code == 403
    assert c.post("/generate", json={"job_text": JOB, "output_type": "cv"}).status_code == 403
    assert c.get("/health").status_code == 200


def test_models_endpoint_lists_models(make_client, monkeypatch):
    monkeypatch.setattr(
        "app.api.models.list_models", lambda: [{"id": "gemini-x", "name": "Gemini X"}]
    )
    body = make_client().get("/models").json()
    assert body["models"] == [{"id": "gemini-x", "name": "Gemini X"}]
    assert "default" in body


def test_generate_rejects_unknown_model(store, monkeypatch):
    limiter.enabled = False
    app.dependency_overrides[store_dep] = lambda: store
    monkeypatch.setattr("app.api.deps.is_allowed", lambda m: False)
    r = TestClient(app, headers={"X-Privacy-Consent": "1"}).post(
        "/generate?model=inventado", json={"job_text": JOB, "output_type": "cv"}
    )
    app.dependency_overrides.clear()
    limiter.enabled = True
    assert r.status_code == 422


def test_models_ranking_and_filtering(monkeypatch):
    from types import SimpleNamespace as NS

    from app.agent import models as m

    def mk(name, actions=("generateContent",)):
        return NS(name=f"models/{name}", display_name=name, supported_actions=list(actions))

    class FakeClient:
        def __init__(self, **_):
            self.models = NS(
                list=lambda: [
                    mk("gemini-9-pro"),
                    mk("gemini-9-flash"),
                    mk("gemini-9-flash-tts"),
                    mk("gemini-9-embedding", ("embedContent",)),
                    mk("gemma-3"),
                    mk("gemini-9-flash-lite"),
                ]
            )

    monkeypatch.setattr(m.genai, "Client", FakeClient)
    monkeypatch.setattr(m.settings, "google_api_key", "x")
    ids = [x["id"] for x in m.fetch_models()]
    assert ids == ["gemini-9-flash-lite", "gemini-9-flash", "gemini-9-pro"]


def test_missing_api_key_is_503(store, monkeypatch):
    limiter.enabled = False
    app.dependency_overrides[store_dep] = lambda: store
    monkeypatch.setattr("app.agent.llm.settings.google_api_key", "")
    app.dependency_overrides.pop(llm_dep, None)
    r = TestClient(app, headers={"X-Privacy-Consent": "1"}).post(
        "/generate", json={"job_text": JOB, "output_type": "cv"}
    )
    app.dependency_overrides.clear()
    limiter.enabled = True
    assert r.status_code == 503


def test_frontend_is_served_without_shadowing_api(make_client):
    c = make_client()
    page = c.get("/")
    assert page.status_code == 200 and "Portafolio Adaptativo" in page.text
    assert c.get("/app.js").status_code == 200
    assert c.get("/styles.css").status_code == 200
    assert c.get("/health").json() == {"status": "ok"}


def test_rate_limit_returns_429(make_client, monkeypatch):
    c = make_client()
    limiter.enabled = True
    limiter.reset()
    codes = [c.post("/ingest", files={"file": ("a.docx", b"x")}).status_code for _ in range(25)]
    assert 429 in codes
