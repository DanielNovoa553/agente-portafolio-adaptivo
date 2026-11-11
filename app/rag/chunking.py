import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


@dataclass
class Chunk:
    """Fragmento de texto indexable junto con su sección de origen.

    Attributes:
        text: Contenido del fragmento, con el título de la sección como prefijo.
        section: Título de la sección Markdown de la que procede, o cadena vacía.
    """

    text: str
    section: str


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    """Trocea un texto largo en fragmentos que respetan los límites de palabras.

    Args:
        text: Texto que se va a dividir.
        max_chars: Longitud máxima deseada de cada fragmento.
        overlap: Caracteres que se solapan entre fragmentos consecutivos.

    Returns:
        Lista de fragmentos no vacíos; contiene el texto completo si ya cabía.
    """
    if len(text) <= max_chars:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + max_chars // 2, end)
            if cut == -1:
                cut = text.rfind(" ", start + max_chars // 2, end)
            end = cut if cut != -1 else end
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in parts if p]


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[Chunk]:
    """Divide por encabezados Markdown y luego por tamaño, conservando la sección.

    Args:
        text: Documento completo que se va a fragmentar.
        max_chars: Longitud máxima de cada fragmento.
        overlap: Caracteres de solapamiento entre fragmentos de una misma sección.

    Returns:
        Fragmentos en orden, con el título de su sección como prefijo del texto.
    """
    matches = list(HEADING.finditer(text))
    sections: list[tuple[str, str]] = []
    if not matches:
        sections.append(("", text))
    else:
        if text[: matches[0].start()].strip():
            sections.append(("", text[: matches[0].start()]))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sections.append((m.group(2).strip(), text[m.end() : end]))

    chunks: list[Chunk] = []
    for title, body in sections:
        body = body.strip()
        if not body:
            continue
        prefix = f"{title}\n" if title else ""
        for piece in _split_long(body, max_chars, overlap):
            chunks.append(Chunk(text=prefix + piece, section=title))
    return chunks
