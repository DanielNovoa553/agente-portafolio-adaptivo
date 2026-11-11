from pydantic import BaseModel, Field


class JobAnalysis(BaseModel):
    """Análisis estructurado de una oferta de empleo.

    Attributes:
        title: Puesto o título de la vacante.
        required_skills: Habilidades imprescindibles de la oferta.
        nice_to_have: Habilidades deseables pero no obligatorias.
        keywords: Palabras clave relevantes para adaptar el documento.
        seniority: Nivel de seniority detectado, si se indica.
    """

    title: str
    required_skills: list[str] = Field(min_length=1)
    nice_to_have: list[str] = []
    keywords: list[str] = Field(min_length=1)
    seniority: str | None = None


class ExperienceItem(BaseModel):
    """Entrada de experiencia laboral de un CV adaptado.

    Attributes:
        role: Puesto desempeñado.
        company: Empresa en la que se trabajó.
        bullets: Logros o responsabilidades redactados como viñetas.
        evidence_ids: IDs de fragmentos del perfil que respaldan el contenido.
    """

    role: str
    company: str
    bullets: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(
        min_length=1, description="IDs de fragmentos del perfil que respaldan el contenido"
    )


class TailoredCV(BaseModel):
    """CV adaptado a una oferta concreta.

    Attributes:
        headline: Titular profesional que resume el perfil.
        summary: Resumen profesional, con un máximo de 600 caracteres.
        skills: Habilidades destacadas para la vacante.
        experience: Experiencia laboral relevante.
    """

    headline: str
    summary: str = Field(max_length=600)
    skills: list[str] = Field(min_length=1)
    experience: list[ExperienceItem] = []


class ProjectSummary(BaseModel):
    """Proyecto priorizado según su relevancia para la oferta.

    Attributes:
        name: Nombre del proyecto.
        relevance_score: Relevancia para la oferta, entre 0 y 1.
        why_relevant: Justificación de por qué encaja con la vacante.
        evidence_ids: IDs de fragmentos del perfil que respaldan el proyecto.
    """

    name: str
    relevance_score: float = Field(ge=0, le=1)
    why_relevant: str
    evidence_ids: list[str] = Field(min_length=1)


class CoverLetter(BaseModel):
    """Carta de presentación adaptada a una oferta.

    Attributes:
        greeting: Saludo inicial de la carta.
        body: Párrafos del cuerpo, entre uno y cinco.
        closing: Despedida de la carta.
        evidence_ids: IDs de fragmentos del perfil que respaldan el contenido.
    """

    greeting: str
    body: list[str] = Field(min_length=1, max_length=5)
    closing: str
    evidence_ids: list[str] = Field(min_length=1)
