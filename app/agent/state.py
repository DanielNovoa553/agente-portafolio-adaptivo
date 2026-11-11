from enum import Enum
from typing import TypedDict

from pydantic import BaseModel

from app.schemas.documents import (
    CoverLetter,
    JobAnalysis,
    ProjectSummary,
    TailoredCV,
)
from app.schemas.evidence import Evidence


class OutputType(str, Enum):
    """Documento que puede generar el agente.

    Attributes:
        cv: Currículum adaptado a la oferta.
        projects: Proyectos del perfil priorizados por relevancia.
        cover_letter: Carta de presentación adaptada.
    """
    cv = "cv"
    projects = "projects"
    cover_letter = "cover_letter"


class ProjectRanking(BaseModel):
    """Salida estructurada con los proyectos priorizados.

    Attributes:
        projects: Proyectos seleccionados y ordenados por relevancia.
    """
    projects: list[ProjectSummary]


OUTPUT_SCHEMAS: dict[OutputType, type[BaseModel]] = {
    OutputType.cv: TailoredCV,
    OutputType.projects: ProjectRanking,
    OutputType.cover_letter: CoverLetter,
}


class AgentState(TypedDict, total=False):
    """Estado compartido por los nodos del grafo del agente.

    Al ser `total=False`, todos los campos son opcionales y cada nodo devuelve
    solo las claves que actualiza.

    Attributes:
        job_text: Texto completo de la oferta de empleo.
        output_type: Documento que se quiere generar.
        analysis: Análisis estructurado de la oferta.
        evidence: Evidencias del perfil recuperadas para la oferta.
        draft: Borrador generado, o `None` si la validación falló.
        feedback: Errores del intento anterior que debe corregir el modelo.
        attempts: Número de intentos de generación realizados.
        status: Estado del flujo (`ok`, `insufficient_evidence` o `failed`).
        error: Mensaje de error para los estados no exitosos.
    """
    job_text: str
    output_type: OutputType
    analysis: JobAnalysis
    evidence: list[Evidence]
    draft: BaseModel
    feedback: str
    attempts: int
    status: str  # ok | insufficient_evidence | failed
    error: str
