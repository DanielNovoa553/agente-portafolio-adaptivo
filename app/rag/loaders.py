import io
from pathlib import Path

from pypdf import PdfReader

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}


class UnsupportedFileError(ValueError):
    """El archivo no tiene un formato soportado o no contiene texto extraíble."""

    pass


def load_text(filename: str, content: bytes) -> str:
    """Extrae el texto de un archivo Markdown, TXT o PDF.

    Args:
        filename: Nombre del archivo, usado para determinar la extensión.
        content: Contenido binario del archivo.

    Returns:
        Texto extraído y sin espacios en los extremos.

    Raises:
        UnsupportedFileError: Si la extensión no está soportada o no hay texto.
    """
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            f"Formato no soportado: '{ext}'. Usa {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        text = content.decode("utf-8", errors="replace")
    text = text.strip()
    if not text:
        raise UnsupportedFileError("El archivo no contiene texto extraíble")
    return text
