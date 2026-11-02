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
    """Devuelve respuestas guionadas por esquema; registra las llamadas."""

    def __init__(self, scripted: dict):
        self.scripted = {k: list(v) for k, v in scripted.items()}
        self.calls: list[tuple[str, str]] = []

    def with_structured_output(self, schema):
        outer = self

        class Runner:
            def invoke(self, messages):
                outer.calls.append((schema.__name__, messages[-1][1]))
                item = outer.scripted[schema.__name__].pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        return Runner()


@pytest.fixture(autouse=True)
def low_threshold(monkeypatch):
    monkeypatch.setattr("app.agent.graph.MIN_SCORE", 0.1)
    monkeypatch.setattr("app.agent.graph.MIN_TOP_SCORE", 0.0)


@pytest.fixture
def store():
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
    llm = FakeLLM({"JobAnalysis": [ANALYSIS], "TailoredCV": [cv_with(["cv.md::0"])]})
    out = run_agent(llm, store, "Buscamos dev Python FastAPI", OutputType.cv)
    assert out["status"] == "ok"
    assert out["attempts"] == 1
    assert collect_evidence_ids(out["draft"]) == {"cv.md::0"}


def test_retries_when_evidence_id_is_invented(store):
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
    empty = ProfileStore(path="", embedding_function=FakeEmbedder())
    try:
        llm = FakeLLM({"JobAnalysis": [ANALYSIS]})
        out = run_agent(llm, empty, "Buscamos dev Python", OutputType.cover_letter)
        assert out["status"] == "insufficient_evidence"
        assert all(name == "JobAnalysis" for name, _ in llm.calls)
    finally:
        empty.reset()


def test_cover_letter_schema_is_used(store):
    letter = CoverLetter(
        greeting="Hola", body=["P1"], closing="Saludos", evidence_ids=["cv.md::0"]
    )
    llm = FakeLLM({"JobAnalysis": [ANALYSIS], "CoverLetter": [letter]})
    out = run_agent(llm, store, "Buscamos dev Python FastAPI", OutputType.cover_letter)
    assert out["status"] == "ok"
    assert isinstance(out["draft"], CoverLetter)
