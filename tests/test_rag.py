import hashlib
import math

import pytest

from app.rag.chunking import chunk_text
from app.rag.ingest import ingest_file
from app.rag.loaders import UnsupportedFileError, load_text
from app.rag.store import ProfileStore
from app.schemas.evidence import DocType


class FakeEmbedder:
    """Embedding determinista por bolsa de palabras (sin descargar modelos)."""

    DIM = 256

    def __call__(self, input):
        out = []
        for text in input:
            v = [0.0] * self.DIM
            for w in text.lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.DIM] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out

    def name(self):
        return "fake"

    def embed_query(self, input):
        return self(input)

    def embed_documents(self, input):
        return self(input)


@pytest.fixture
def store():
    s = ProfileStore(path="", embedding_function=FakeEmbedder())
    yield s
    s.reset()


CV = b"""# CV
## Experiencia
Desarrollador Python con FastAPI y PostgreSQL en una startup.
## Educacion
Ingenieria de sistemas.
"""


def test_chunk_by_headings():
    chunks = chunk_text(CV.decode())
    assert [c.section for c in chunks] == ["Experiencia", "Educacion"]


def test_chunk_long_text_is_split():
    chunks = chunk_text("palabra " * 500, max_chars=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)


def test_load_rejects_unsupported():
    with pytest.raises(UnsupportedFileError):
        load_text("cv.docx", b"x")


def test_ingest_and_retrieve(store):
    n = ingest_file(store, "cv.md", CV, DocType.cv)
    assert n == 2
    res = store.retrieve("experiencia con FastAPI y Python", k=1)
    assert res[0].section == "Experiencia"
    assert res[0].doc_type == DocType.cv
    assert res[0].id.startswith("cv.md::")


def test_reingest_is_idempotent(store):
    ingest_file(store, "cv.md", CV, DocType.cv)
    ingest_file(store, "cv.md", CV, DocType.cv)
    assert store.count() == 2


def test_filter_by_doc_type(store):
    ingest_file(store, "cv.md", CV, DocType.cv)
    ingest_file(store, "p.md", b"# Proyecto\nAgente con LangGraph", DocType.project)
    res = store.retrieve("LangGraph", k=5, doc_type=DocType.project)
    assert res and all(r.doc_type == DocType.project for r in res)


def test_empty_store_returns_nothing(store):
    assert store.retrieve("lo que sea") == []
