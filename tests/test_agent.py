import pytest

from app.agent.graph import MAX_ATTEMPTS, collect_evidence_ids, run_agent
from app.agent.state import OutputType
from app.rag.ingest import ingest_file
from app.rag.store import ProfileStore
from app.schemas.documents import CoverLetter, JobAnalysis, TailoredCV
from app.schemas.evidence import DocType
from tests.test_rag import FakeEmbedder

ANALYSIS = JobAnalysis(
    title="Backend Python",
    required_skills=["Python", "FastAPI"],
    keywords=["Python", "FastAPI", "API"],
)


class FakeLLM:
    """LLM falso que devuelve respuestas guionadas por nombre de esquema.

    Attributes:
        scripted: Respuestas (o excepciones) pendientes por esquema, en orden de consumo.
        calls: Pares (esquema, último mensaje) registrados en cada invocación.
    """

    def __init__(self, scripted: dict):
        """Inicializa el guion copiando las listas para no mutar el diccionario original.

        Args:
            scripted: Respuestas o excepciones indexadas por el nombre del esquema.
        """
        self.scripted = {k: list(v) for k, v in scripted.items()}
        self.calls: list[tuple[str, str]] = []

    def with_structured_output(self, schema):
        """Devuelve un invocador que consume la siguiente respuesta del esquema indicado.

        Args:
            schema: Esquema Pydantic cuyo nombre selecciona el guion que se consume.

        Returns:
            Runner asociado al esquema, listo para invocar con mensajes.
        """
        outer = self

        class Runner:
            """Invocador de un único esquema que registra la llamada y consume su guion."""

            def invoke(self, messages):
                """Registra el último mensaje, consume la siguiente respuesta y la devuelve.

                Args:
                    messages: Mensajes enviados al LLM; se registra el último.

                Returns:
                    La respuesta guionada para el esquema.

                Raises:
                    Exception: La excepción guionada, si el siguiente elemento es una excepción.
                """
                outer.calls.append((schema.__name__, messages[-1][1]))
                item = outer.scripted[schema.__name__].pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        return Runner()


@pytest.fixture(autouse=True)
def low_threshold(monkeypatch):
    """Rebaja los umbrales de recuperación para que la evidencia del CV sea suficiente.

    Args:
        monkeypatch: Fixture de pytest que parchea los umbrales del grafo.
    """
    monkeypatch.setattr("app.agent.graph.MIN_SCORE", 0.1)
    monkeypatch.setattr("app.agent.graph.MIN_TOP_SCORE", 0.0)


@pytest.fixture
def store():
    """Crea un perfil temporal con un CV indexado y lo limpia al terminar.

    Yields:
        ProfileStore efímero con dos fragmentos de `cv.md`.
    """
    s = ProfileStore(path="", embedding_function=FakeEmbedder())
    ingest_file(
        s,
        "cv.md",
        b"# Experiencia\nDesarrollador Python con FastAPI creando una API REST.\n"
        b"# Skills\nPython FastAPI API Docker",
        DocType.cv,
    )
    yield s
    s.reset()


def cv_with(ids):
    """Construye un CV adaptado con evidencia en una experiencia o sin experiencia.

    Args:
        ids: Identificadores de evidencia para el único bullet; si está vacío,
            el CV se devuelve sin experiencia.

    Returns:
        TailoredCV con encabezado y resumen mínimos para las pruebas.
    """
    return TailoredCV(
        headline="Dev",
        summary="Resumen",
        skills=["Python"],
        experience=[],
    ) if not ids else TailoredCV(
        headline="Dev",
        summary="Resumen",
        skills=["Python"],
        experience=[
            {"role": "Dev", "company": "X", "bullets": ["API"], "evidence_ids": ids}
        ],
    )


def test_happy_path(store):
    """Verifica que el agente genera un CV válido en el primer intento.

    Args:
        store: Perfil indexado con un fragmento de CV que aporta evidencia válida.
    """
    llm = FakeLLM({"JobAnalysis": [ANALYSIS], "TailoredCV": [cv_with(["cv.md::0"])]})
    out = run_agent(llm, store, "Buscamos dev Python FastAPI", OutputType.cv)
    assert out["status"] == "ok"
    assert out["attempts"] == 1
    assert collect_evidence_ids(out["draft"]) == {"cv.md::0"}


def test_retries_when_evidence_id_is_invented(store):
    """Comprueba que una evidencia inventada provoca un reintento con feedback en el prompt.

    Args:
        store: Perfil indexado con la evidencia real que debe citar el segundo intento.
    """
    llm = FakeLLM(
        {
            "JobAnalysis": [ANALYSIS],
            "TailoredCV": [cv_with(["inventado::9"]), cv_with(["cv.md::0"])],
        }
    )
    out = run_agent(llm, store, "Buscamos dev Python FastAPI", OutputType.cv)
    assert out["status"] == "ok"
    assert out["attempts"] == 2
    assert "inventado::9" in llm.calls[-1][1]  # el feedback llegó al prompt


def test_fails_after_max_attempts(store):
    """Verifica que el agente falla tras agotar MAX_ATTEMPTS con evidencia inventada.

    Args:
        store: Perfil indexado que sirve de contexto de recuperación.
    """
    llm = FakeLLM(
        {
            "JobAnalysis": [ANALYSIS],
            "TailoredCV": [cv_with(["x::1"]) for _ in range(MAX_ATTEMPTS)],
        }
    )
    out = run_agent(llm, store, "Buscamos dev Python FastAPI", OutputType.cv)
    assert out["status"] == "failed"
    assert out["attempts"] == MAX_ATTEMPTS


def test_insufficient_evidence_skips_generation():
    """Comprueba que sin evidencias recuperadas no se genera borrador y se avisa del déficit."""
    empty = ProfileStore(path="", embedding_function=FakeEmbedder())
    try:
        llm = FakeLLM({"JobAnalysis": [ANALYSIS]})
        out = run_agent(llm, empty, "Buscamos dev Python", OutputType.cover_letter)
        assert out["status"] == "insufficient_evidence"
        assert all(name == "JobAnalysis" for name, _ in llm.calls)
    finally:
        empty.reset()


def test_cover_letter_schema_is_used(store):
    """Verifica que para una carta de presentación se usa el esquema CoverLetter.

    Args:
        store: Perfil indexado con la evidencia que cita la carta generada.
    """
    letter = CoverLetter(
        greeting="Hola", body=["P1"], closing="Saludos", evidence_ids=["cv.md::0"]
    )
    llm = FakeLLM({"JobAnalysis": [ANALYSIS], "CoverLetter": [letter]})
    out = run_agent(llm, store, "Buscamos dev Python FastAPI", OutputType.cover_letter)
    assert out["status"] == "ok"
    assert isinstance(out["draft"], CoverLetter)
