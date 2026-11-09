from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agent.state import OutputType
from app.core.config import settings
from app.schemas.evidence import Evidence


class InterviewCategory(str, Enum):
    technical = "technical"
    project = "project"
    behavioral = "behavioral"
    role_fit = "role_fit"
    gap = "gap"


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class Criterion(str, Enum):
    relevance = "relevance"
    evidence_alignment = "evidence_alignment"
    specificity = "specificity"
    structure = "structure"
    technical_depth = "technical_depth"


class Verdict(str, Enum):
    strong = "strong"
    partial = "partial"
    weak = "weak"


class GeneratedInterviewQuestion(BaseModel):
    category: InterviewCategory
    difficulty: Difficulty
    question: str = Field(min_length=10, max_length=500)
    why_asked: str = Field(min_length=10, max_length=400)
    evidence_ids: list[str] = []
    expected_points: list[str] = Field(min_length=1, max_length=5)


class GeneratedInterviewPlan(BaseModel):
    role_title: str = Field(min_length=1, max_length=200)
    focus_areas: list[str] = Field(min_length=1, max_length=8)
    questions: list[GeneratedInterviewQuestion] = Field(min_length=5, max_length=10)


class InterviewQuestion(BaseModel):
    id: str
    category: InterviewCategory
    difficulty: Difficulty
    question: str
    why_asked: str
    evidence_ids: list[str] = []


class InterviewQuestionInternal(InterviewQuestion):
    expected_points: list[str]


class InterviewPlan(BaseModel):
    role_title: str
    focus_areas: list[str]
    questions: list[InterviewQuestion]


class InterviewPlanInternal(BaseModel):
    role_title: str
    focus_areas: list[str]
    questions: list[InterviewQuestionInternal]


class CriterionScore(BaseModel):
    criterion: Criterion
    score: int = Field(ge=0, le=5)
    comment: str = Field(min_length=1, max_length=300)


class AnswerEvaluation(BaseModel):
    criteria: list[CriterionScore] = Field(min_length=5, max_length=5)
    strengths: list[str] = Field(min_length=1, max_length=3)
    gaps: list[str] = Field(min_length=1, max_length=3)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=5)
    suggested_outline: list[str] = Field(min_length=3, max_length=5)
    evidence_ids: list[str] = []


class SuggestedAnswer(BaseModel):
    answer: str = Field(min_length=40, max_length=4000)
    outline: list[str] = Field(min_length=3, max_length=6)
    evidence_ids: list[str] = []
    caveats: list[str] = Field(default_factory=list, max_length=5)
    preparation_notes: list[str] = Field(default_factory=list, max_length=5)


class InterviewStartRequest(BaseModel):
    job_text: str = Field(min_length=50, max_length=8000)
    output_type: OutputType
    document: dict[str, Any]
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    question_count: int = Field(
        default_factory=lambda: settings.interview_question_count, ge=5, le=10
    )


class InterviewStartResponse(BaseModel):
    session_id: str
    expires_at: datetime
    role_title: str
    focus_areas: list[str]
    questions: list[InterviewQuestion]


class InterviewAnswerRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=20)
    # La longitud se valida en el servicio para no devolver el texto en errores 422.
    answer: str


class InterviewAnswerResponse(BaseModel):
    question_id: str
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    criteria: list[CriterionScore]
    strengths: list[str]
    gaps: list[str]
    unsupported_claims: list[str]
    suggested_outline: list[str]
    evidence: list[Evidence]
    answered: int
    total: int
    next_question_id: str | None


class InterviewSuggestionResponse(BaseModel):
    question_id: str
    answer: str
    outline: list[str]
    caveats: list[str]
    preparation_notes: list[str]
    evidence: list[Evidence]


class InterviewSummary(BaseModel):
    answered: int
    total: int
    completed: bool
    overall_score: int
    by_category: dict[str, int]
    top_strengths: list[str]
    top_gaps: list[str]
    next_steps: list[str]
    unanswered_question_ids: list[str]
