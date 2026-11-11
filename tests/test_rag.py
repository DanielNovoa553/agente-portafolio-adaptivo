import hashlib
import math

import pytest

from app.rag.chunking import chunk_text
from app.rag.ingest import ingest_file
from app.rag.loaders import UnsupportedFileError, load_text
from app.rag.store import ProfileStore
from app.schemas.evidence import DocType


class FakeEmbedder:
    """Embedding determinista por bolsa de palabras (sin descargar modelos).

    Attributes:
        DIM: Dimensión fija de los vectores generados.
    """

    DIM = 256

    def __call__(self, input):
        """Convierte textos en vectores normalizados de bolsa de palabras.

        Args:
            input: Lista de textos que se vectorizan.

        Returns:
            Lista de vectores normalizados, uno por cada texto.
        """
        out = []
        for text in input:
            v = [0.0] * self.DIM
            for w in text.lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.DIM] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out

    def name(self):
        """Devuelve el nombre del embedder usado por la tienda.

        Returns:
            Cadena fija "fake".
        """
        return "fake"

    def embed_query(self, input):
        """Vectoriza una consulta con el mismo esquema que los documentos.

        Args:
            input: Texto o lista de textos de la consulta.

        Returns:
            Vectores normalizados producidos por `__call__`.
        """
        return self(input)

    def embed_documents(self, input):
        """Vectoriza documentos para indexarlos en la tienda.

        Args:
            input: Lista de textos de los documentos.

        Returns:
            Vectores normalizados producidos por `__call__`.
        """
        return self(input)


@pytest.fixture
def store():
    """Proporciona una tienda de perfil efímera con el embedder falso.

    Yields:
        ProfileStore vacía respaldada por una colección Chroma temporal.
    """
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
    """Verifica que el chunking separa por encabezados Markdown y conserva la sección."""
    chunks = chunk_text(CV.decode())
    assert [c.section for c in chunks] == ["Experiencia", "Educacion"]


def test_chunk_long_text_is_split():
    """Verifica que un texto largo se divide en fragmentos dentro del tamaño máximo."""
    chunks = chunk_text("palabra " * 500, max_chars=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)


def test_load_rejects_unsupported():
    """Verifica que una extensión no soportada lanza UnsupportedFileError."""
    with pytest.raises(UnsupportedFileError):
        load_text("cv.docx", b"x")


def test_ingest_and_retrieve(store):
    """Verifica que ingerir un CV produce fragmentos recuperables con su sección.

    Args:
        store: Tienda de perfil efímera con el embedder falso.
    """
    n = ingest_file(store, "cv.md", CV, DocType.cv)
    assert n == 2
    res = store.retrieve("experiencia con FastAPI y Python", k=1)
    assert res[0].section == "Experiencia"
    assert res[0].doc_type == DocType.cv
    assert res[0].id.startswith("cv.md::")


def test_reingest_is_idempotent(store):
    """Verifica que reingerir el mismo archivo no duplica los fragmentos.

    Args:
        store: Tienda de perfil efímera con el embedder falso.
    """
    ingest_file(store, "cv.md", CV, DocType.cv)
    ingest_file(store, "cv.md", CV, DocType.cv)
    assert store.count() == 2


def test_filter_by_doc_type(store):
    """Verifica que la recuperación filtra por doc_type y solo devuelve proyectos.

    Args:
        store: Tienda de perfil efímera con el embedder falso.
    """
    ingest_file(store, "cv.md", CV, DocType.cv)
    ingest_file(store, "p.md", b"# Proyecto\nAgente con LangGraph", DocType.project)
    res = store.retrieve("LangGraph", k=5, doc_type=DocType.project)
    assert res and all(r.doc_type == DocType.project for r in res)


def test_empty_store_returns_nothing(store):
    """Verifica que una tienda vacía no devuelve resultados.

    Args:
        store: Tienda de perfil efímera con el embedder falso.
    """
    assert store.retrieve("lo que sea") == []
