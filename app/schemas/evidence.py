from enum import Enum

from pydantic import BaseModel, Field


class DocType(str, Enum):
    """Tipo de documento del perfil que origina un fragmento.

    Attributes:
        cv: Currículum vitae.
        project: Descripción de un proyecto.
        certification: Certificación profesional.
        other: Cualquier otro documento de apoyo.
    """

    cv = "cv"
    project = "project"
    certification = "certification"
    other = "other"


class Evidence(BaseModel):
    """Fragmento del perfil recuperado para respaldar una respuesta.

    Attributes:
        id: Identificador único del fragmento, con formato `archivo::n`.
        text: Contenido textual del fragmento.
        doc_type: Tipo de documento del que procede.
        source: Nombre del archivo de origen.
        section: Sección Markdown del documento, o cadena vacía.
        score: Similitud con la consulta (1 = idéntico).
    """

    id: str
    text: str
    doc_type: DocType
    source: str
    section: str = ""
    score: float = Field(description="Similitud (1 = idéntico)")
