from typing import Any

from pydantic import BaseModel

from app.schemas.evidence import Evidence


def collect_evidence_ids(obj: Any) -> set[str]:
    """Reúne todos los `evidence_ids` de un modelo (incluidos los anidados)."""
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
    return "\n\n".join(
        f"[ID: {e.id}] ({e.doc_type.value} / {e.section or 'general'})\n{e.text}"
        for e in evidence
    )
