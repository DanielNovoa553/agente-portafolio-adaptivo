from typing import Any

from pydantic import BaseModel

from app.schemas.evidence import Evidence


def collect_evidence_ids(obj: Any) -> set[str]:
    """Reúne todos los `evidence_ids` de un modelo, incluidas las estructuras anidadas.

    Recorre modelos Pydantic y listas; cualquier otro tipo de valor se ignora.
    El resultado no conserva orden ni duplicados.

    Args:
        obj: Modelo, lista o valor escalar que se quiere inspeccionar.

    Returns:
        Conjunto con los IDs de evidencia encontrados.
    """
    ids: set[str] = set()
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            value = getattr(obj, name)
            if name == "evidence_ids":
                ids.update(value)
            else:
                ids |= collect_evidence_ids(value)
    elif isinstance(obj, list):
        for item in obj:
            ids |= collect_evidence_ids(item)
    return ids


def format_evidence(evidence: list[Evidence]) -> str:
    """Formatea las evidencias como bloques de texto para un prompt.

    Cada bloque incluye el ID, el tipo de documento, la sección (o `general`)
    y el texto completo de la evidencia.

    Args:
        evidence: Evidencias recuperadas que se van a incluir en el prompt.

    Returns:
        Bloques separados por una línea en blanco.
    """
    return "\n\n".join(
        f"[ID: {e.id}] ({e.doc_type.value} / {e.section or 'general'})\n{e.text}"
        for e in evidence
    )
