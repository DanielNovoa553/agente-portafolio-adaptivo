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
    """Crea un perfil Chroma efímero y lo limpia al finalizar la prueba.

    Yields:
        ProfileStore vacío listo para ingerir documentos.
    """
    s = ProfileStore(path="", embedding_function=FakeEmbedder())
    yield s
    s.reset()


@pytest.fixture
def make_client(store, monkeypatch):
    """Configura umbrales, desactiva el rate limit y restaura el estado al terminar.

    Args:
        store: Perfil efímero que se inyecta como dependencia de la API.
        monkeypatch: Fixture de pytest para rebajar los umbrales del grafo.

    Yields:
        Factoría que crea un TestClient con consentimiento y dependencias sustituidas.
    """
    monkeypatch.setattr("app.agent.graph.MIN_SCORE", 0.1)
    monkeypatch.setattr("app.agent.graph.MIN_TOP_SCORE", 0.0)
    limiter.enabled = False
    limiter.reset()

    def _make(llm=None):
        """Crea un TestClient sustituyendo el perfil y el LLM indicados.

        Args:
            llm: LLM falso que se inyecta; por defecto, None.

        Returns:
            TestClient de la app con la cabecera de consentimiento.
        """
        app.dependency_overrides[store_dep] = lambda: store
        app.dependency_overrides[llm_dep] = lambda: llm
        return TestClient(app, headers={"X-Privacy-Consent": "2"})

    yield _make
    app.dependency_overrides.clear()
    limiter.enabled = True


def test_ingest_list_and_delete(make_client):
    """Comprueba la ingesta, el listado de fuentes y que el segundo borrado da 404.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
    """
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
    """Verifica que la ingesta rechaza con 415 un formato no admitido como .docx.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
    """
    r = make_client().post("/ingest", files={"file": ("cv.docx", b"x")})
    assert r.status_code == 415


def test_ingest_rejects_large_file(make_client, monkeypatch):
    """Comprueba que un archivo mayor que max_upload_bytes se rechaza con 413.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
        monkeypatch: Fixture de pytest que reduce el límite de subida a 10 bytes.
    """
    monkeypatch.setattr("app.api.profile.settings.max_upload_bytes", 10)
    r = make_client().post("/ingest", files={"file": ("cv.md", b"x" * 50)})
    assert r.status_code == 413


def test_ingest_path_traversal_name_is_sanitized(make_client):
    """Verifica que un nombre con path traversal se reduce a su nombre base al ingerir.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
    """
    r = make_client().post("/ingest", files={"file": ("../../evil.md", CV_MD)})
    assert r.json()["source"] == "evil.md"


def test_generate_ok_returns_document_and_used_evidence(make_client):
    """Comprueba que /generate devuelve el documento, la evidencia usada y la recuperada.

    También verifica que la respuesta incluye la cabecera X-Request-ID.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
    """
    c = make_client(FakeLLM({"JobAnalysis": [ANALYSIS], "TailoredCV": [cv_with(["cv.md::0"])]}))
    c.post("/ingest", files={"file": ("cv.md", CV_MD)}, data={"doc_type": "cv"})
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["document"]["headline"] == "Dev"
    assert [e["id"] for e in body["evidence"]] == ["cv.md::0"]
    assert {e["id"] for e in body["retrieved_evidence"]} == {"cv.md::0", "cv.md::1"}
    assert body["attempts"] == 1
    assert "X-Request-ID" in r.headers


def test_generate_validates_input(make_client):
    """Verifica que /generate responde 422 con una oferta corta o un tipo de salida inválido.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
    """
    c = make_client()
    assert c.post("/generate", json={"job_text": "corto", "output_type": "cv"}).status_code == 422
    assert c.post("/generate", json={"job_text": JOB, "output_type": "otro"}).status_code == 422


def test_generate_without_profile_is_422(make_client):
    """Comprueba que generar sin evidencias en el perfil responde 422 con aviso de evidencia.

    Args:
        make_client: Factoría de TestClient con un LLM guionado y el perfil vacío.
    """
    c = make_client(FakeLLM({"JobAnalysis": [ANALYSIS]}))
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 422
    assert "evidencia" in r.json()["detail"]


def test_generate_failed_validation_is_502(make_client):
    """Verifica que /generate responde 502 si el LLM cita evidencia inventada tras reintentar.

    Args:
        make_client: Factoría de TestClient con un LLM que siempre inventa evidencia.
    """
    llm = FakeLLM({"JobAnalysis": [ANALYSIS], "TailoredCV": [cv_with(["x::1"])] * 3})
    c = make_client(llm)
    c.post("/ingest", files={"file": ("cv.md", CV_MD)}, data={"doc_type": "cv"})
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 502


def test_upstream_llm_error_is_503(make_client):
    """Comprueba que un error del proveedor LLM se traduce en 503 con aviso de saturación.

    Args:
        make_client: Factoría de TestClient con un LLM que falla al configurarse.
    """
    from langchain_google_genai._common import GoogleGenerativeAIError

    class BrokenLLM:
        """LLM falso cuyo enlace con el proveedor falla al pedir salida estructurada."""

        def with_structured_output(self, *_a, **_k):
            """Simula un proveedor no disponible al configurar la salida estructurada.

            Args:
                *_a: Argumentos posicionales ignorados.
                **_k: Argumentos con nombre ignorados.

            Raises:
                GoogleGenerativeAIError: Siempre, con un mensaje 503 UNAVAILABLE.
            """
            raise GoogleGenerativeAIError("503 UNAVAILABLE")

    c = make_client(BrokenLLM())
    c.post("/ingest", files={"file": ("cv.md", CV_MD)}, data={"doc_type": "cv"})
    r = c.post("/generate", json={"job_text": JOB, "output_type": "cv"})
    assert r.status_code == 503
    assert "saturado" in r.json()["detail"]


def test_requires_privacy_consent(make_client):
    """Verifica que las rutas protegidas exigen X-Privacy-Consent: 2 y /health no.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
    """
    c = make_client()
    c.headers.pop("X-Privacy-Consent")
    assert c.get("/sources").status_code == 403
    assert c.post("/ingest", files={"file": ("cv.md", CV_MD)}).status_code == 403
    assert c.post("/generate", json={"job_text": JOB, "output_type": "cv"}).status_code == 403
    assert c.get("/health").status_code == 200


def test_models_endpoint_lists_models(make_client, monkeypatch):
    """Comprueba que /models devuelve los modelos del proveedor y el modelo por defecto.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
        monkeypatch: Fixture de pytest que sustituye el listado real de modelos.
    """
    monkeypatch.setattr(
        "app.api.models.list_models", lambda: [{"id": "gemini-x", "name": "Gemini X"}]
    )
    body = make_client().get("/models").json()
    assert body["models"] == [{"id": "gemini-x", "name": "Gemini X"}]
    assert "default" in body


def test_generate_rejects_unknown_model(store, monkeypatch):
    """Verifica que /generate rechaza con 422 un modelo que no está en la lista permitida.

    Args:
        store: Perfil efímero que se inyecta como dependencia.
        monkeypatch: Fixture de pytest que fuerza is_allowed a rechazar cualquier modelo.
    """
    limiter.enabled = False
    app.dependency_overrides[store_dep] = lambda: store
    monkeypatch.setattr("app.api.deps.is_allowed", lambda m: False)
    r = TestClient(app, headers={"X-Privacy-Consent": "2"}).post(
        "/generate?model=inventado", json={"job_text": JOB, "output_type": "cv"}
    )
    app.dependency_overrides.clear()
    limiter.enabled = True
    assert r.status_code == 422


def test_models_ranking_and_filtering(monkeypatch):
    """Comprueba que fetch_models filtra no generativos y ordena flash-lite, flash y pro.

    Args:
        monkeypatch: Fixture de pytest que sustituye el cliente de Gemini y la API key.
    """
    from types import SimpleNamespace as NS

    from app.agent import models as m

    def mk(name, actions=("generateContent",)):
        """Crea un modelo simulado con nombre, nombre visible y acciones soportadas.

        Args:
            name: Identificador corto del modelo.
            actions: Acciones soportadas; por defecto, generateContent.

        Returns:
            SimpleNamespace con la forma esperada por fetch_models.
        """
        return NS(name=f"models/{name}", display_name=name, supported_actions=list(actions))

    class FakeClient:
        """Cliente de Gemini falso que expone un catálogo fijo de modelos.

        Attributes:
            models: Objeto con list() que devuelve los modelos simulados.
        """

        def __init__(self, **_):
            """Inicializa el catálogo de modelos simulados.

            Args:
                **_: Argumentos del cliente real que se ignoran.
            """
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
    """Comprueba que /generate responde 503 cuando no hay GOOGLE_API_KEY configurada.

    Args:
        store: Perfil efímero que se inyecta como dependencia.
        monkeypatch: Fixture de pytest que vacía la API key y elimina el override del LLM.
    """
    limiter.enabled = False
    app.dependency_overrides[store_dep] = lambda: store
    monkeypatch.setattr("app.agent.llm.settings.google_api_key", "")
    app.dependency_overrides.pop(llm_dep, None)
    r = TestClient(app, headers={"X-Privacy-Consent": "2"}).post(
        "/generate", json={"job_text": JOB, "output_type": "cv"}
    )
    app.dependency_overrides.clear()
    limiter.enabled = True
    assert r.status_code == 503


def test_frontend_is_served_without_shadowing_api(make_client):
    """Verifica que los estáticos se sirven en la raíz sin tapar las rutas de la API.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
    """
    c = make_client()
    page = c.get("/")
    assert page.status_code == 200 and "Portafolio Adaptativo" in page.text
    assert c.get("/app.js").status_code == 200
    assert c.get("/styles.css").status_code == 200
    assert c.get("/health").json() == {"status": "ok"}


def test_rate_limit_returns_429(make_client, monkeypatch):
    """Comprueba que /ingest devuelve 429 al superar el límite de peticiones por IP.

    Args:
        make_client: Factoría de TestClient con el perfil efímero inyectado.
        monkeypatch: Fixture de pytest presente en la firma pero no utilizada.
    """
    c = make_client()
    limiter.enabled = True
    limiter.reset()
    codes = [c.post("/ingest", files={"file": ("a.docx", b"x")}).status_code for _ in range(25)]
    assert 429 in codes
