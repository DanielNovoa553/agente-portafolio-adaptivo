from typing import Any

from pydantic import BaseModel, Field

from app.agent.state import OutputType
from app.schemas.evidence import DocType, Evidence


class GenerateRequest(BaseModel):
    """Petición para generar un documento adaptado a una oferta.

    Attributes:
        job_text: Texto de la oferta de empleo.
        output_type: Tipo de documento que se va a generar.
    """

    job_text: str = Field(min_length=50, max_length=8000)
    output_type: OutputType


class GenerateResponse(BaseModel):
    """Resultado de la generación de un documento.

    Attributes:
        output_type: Tipo de documento generado.
        document: Documento validado y serializado.
        evidence: Evidencias citadas por el documento.
        retrieved_evidence: Evidencias recuperadas durante la búsqueda.
        attempts: Número de intentos consumidos por el agente.
    """

    output_type: OutputType
    document: dict[str, Any]
    evidence: list[Evidence]
    retrieved_evidence: list[Evidence] = []
    attempts: int


class IngestResponse(BaseModel):
    """Resultado de indexar un archivo del perfil.

    Attributes:
        source: Nombre del archivo indexado.
        doc_type: Tipo de documento asignado.
        chunks: Número de fragmentos generados e indexados.
    """

    source: str
    doc_type: DocType
    chunks: int


class SourcesResponse(BaseModel):
    """Inventario de fuentes indexadas en el perfil.

    Attributes:
        sources: Número de fragmentos indexados por nombre de archivo.
        total_chunks: Número total de fragmentos almacenados.
    """

    sources: dict[str, int]
    total_chunks: int
