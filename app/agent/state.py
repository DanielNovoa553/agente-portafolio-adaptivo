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
    cv = "cv"
    projects = "projects"
    cover_letter = "cover_letter"


class ProjectRanking(BaseModel):
    projects: list[ProjectSummary]


OUTPUT_SCHEMAS: dict[OutputType, type[BaseModel]] = {
    OutputType.cv: TailoredCV,
    OutputType.projects: ProjectRanking,
    OutputType.cover_letter: CoverLetter,
}


class AgentState(TypedDict, total=False):
    job_text: str
    output_type: OutputType
    analysis: JobAnalysis
    evidence: list[Evidence]
    draft: BaseModel
    feedback: str
    attempts: int
    status: str  # ok | insufficient_evidence | failed
    error: str
