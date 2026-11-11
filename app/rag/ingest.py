from app.rag.chunking import chunk_text
from app.rag.loaders import load_text
from app.rag.store import ProfileStore
from app.schemas.evidence import DocType


def ingest_file(
    store: ProfileStore, filename: str, content: bytes, doc_type: DocType
) -> int:
    """Carga, fragmenta e indexa un archivo en el perfil.

    Args:
        store: Almacén del perfil donde se indexan los fragmentos.
        filename: Nombre del archivo, usado como fuente e identificador.
        content: Contenido binario del archivo.
        doc_type: Tipo de documento (`cv`, `project`, `certification` u `other`).

    Returns:
        Número de fragmentos indexados.

    Raises:
        UnsupportedFileError: Si el formato no está soportado o no hay texto.
    """
    text = load_text(filename, content)
    return store.add_document(filename, doc_type, chunk_text(text))
