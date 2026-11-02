from pydantic import BaseModel, Field


class JobAnalysis(BaseModel):
    title: str
    required_skills: list[str] = Field(min_length=1)
    nice_to_have: list[str] = []
    keywords: list[str] = Field(min_length=1)
    seniority: str | None = None


class ExperienceItem(BaseModel):
    role: str
    company: str
    bullets: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(
        min_length=1, description="IDs de fragmentos del perfil que respaldan el contenido"
    )


class TailoredCV(BaseModel):
    headline: str
    summary: str = Field(max_length=600)
    skills: list[str] = Field(min_length=1)
    experience: list[ExperienceItem] = []


class ProjectSummary(BaseModel):
    name: str
    relevance_score: float = Field(ge=0, le=1)
    why_relevant: str
    evidence_ids: list[str] = Field(min_length=1)


class CoverLetter(BaseModel):
    greeting: str
    body: list[str] = Field(min_length=1, max_length=5)
    closing: str
    evidence_ids: list[str] = Field(min_length=1)
