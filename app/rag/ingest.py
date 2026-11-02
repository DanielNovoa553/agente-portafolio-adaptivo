from app.rag.chunking import chunk_text
from app.rag.loaders import load_text
from app.rag.store import ProfileStore
from app.schemas.evidence import DocType


def ingest_file(
    store: ProfileStore, filename: str, content: bytes, doc_type: DocType
) -> int:
    """Carga, fragmenta e indexa un archivo. Devuelve el número de fragmentos."""
    text = load_text(filename, content)
    return store.add_document(filename, doc_type, chunk_text(text))
