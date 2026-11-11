from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agent.state import OutputType
from app.core.config import settings
from app.schemas.evidence import Evidence


class InterviewCategory(str, Enum):
    """Categoría temática de una pregunta de entrevista.

    Attributes:
        technical: Conocimientos técnicos generales.
        project: Experiencia concreta en proyectos.
        behavioral: Comportamiento y habilidades blandas.
        role_fit: Encaje con el puesto y la motivación.
        gap: Carencias detectadas respecto a la oferta.
    """

    technical = "technical"
    project = "project"
    behavioral = "behavioral"
    role_fit = "role_fit"
    gap = "gap"


class Difficulty(str, Enum):
    """Dificultad esperada de una pregunta de entrevista.

    Attributes:
        easy: Nivel básico.
        medium: Nivel intermedio.
        hard: Nivel avanzado.
    """

    easy = "easy"
    medium = "medium"
    hard = "hard"


class Criterion(str, Enum):
    """Criterio de evaluación de una respuesta de entrevista.

    Attributes:
        relevance: Grado en que la respuesta atiende a la pregunta.
        evidence_alignment: Coherencia con la evidencia del perfil.
        specificity: Concreción y detalle de la respuesta.
        structure: Organización y claridad del relato.
        technical_depth: Profundidad técnica demostrada.
    """

    relevance = "relevance"
    evidence_alignment = "evidence_alignment"
    specificity = "specificity"
    structure = "structure"
    technical_depth = "technical_depth"


class Verdict(str, Enum):
    """Valoración global de una respuesta según su puntuación.

    Attributes:
        strong: Respuesta sólida y bien respaldada.
        partial: Respuesta correcta pero incompleta.
        weak: Respuesta insuficiente o poco respaldada.
    """

    strong = "strong"
    partial = "partial"
    weak = "weak"


class GeneratedInterviewQuestion(BaseModel):
    """Pregunta de entrevista tal como la genera el LLM.

    Attributes:
        category: Categoría temática de la pregunta.
        difficulty: Dificultad esperada.
        question: Enunciado de la pregunta, entre 10 y 500 caracteres.
        why_asked: Motivo por el que se plantea, entre 10 y 400 caracteres.
        evidence_ids: IDs de evidencias del perfil relacionadas.
        expected_points: Puntos que debería cubrir una buena respuesta.
    """

    category: InterviewCategory
    difficulty: Difficulty
    question: str = Field(min_length=10, max_length=500)
    why_asked: str = Field(min_length=10, max_length=400)
    evidence_ids: list[str] = []
    expected_points: list[str] = Field(min_length=1, max_length=5)


class GeneratedInterviewPlan(BaseModel):
    """Plan de entrevista generado por el LLM antes de validarlo.

    Attributes:
        role_title: Título del puesto al que se orienta la entrevista.
        focus_areas: Áreas de conocimiento en las que se enfoca.
        questions: Preguntas propuestas, entre 5 y 10.
    """

    role_title: str = Field(min_length=1, max_length=200)
    focus_areas: list[str] = Field(min_length=1, max_length=8)
    questions: list[GeneratedInterviewQuestion] = Field(min_length=5, max_length=10)


class InterviewQuestion(BaseModel):
    """Pregunta de entrevista expuesta al cliente, sin puntos esperados.

    Attributes:
        id: Identificador de la pregunta dentro de la sesión.
        category: Categoría temática de la pregunta.
        difficulty: Dificultad esperada.
        question: Enunciado de la pregunta.
        why_asked: Motivo por el que se plantea.
        evidence_ids: IDs de evidencias del perfil relacionadas.
    """

    id: str
    category: InterviewCategory
    difficulty: Difficulty
    question: str
    why_asked: str
    evidence_ids: list[str] = []


class InterviewQuestionInternal(InterviewQuestion):
    """Pregunta de entrevista con los puntos esperados, para uso interno.

    Attributes:
        id: Identificador de la pregunta dentro de la sesión.
        category: Categoría temática de la pregunta.
        difficulty: Dificultad esperada.
        question: Enunciado de la pregunta.
        why_asked: Motivo por el que se plantea.
        evidence_ids: IDs de evidencias del perfil relacionadas.
        expected_points: Puntos que debería cubrir una buena respuesta.
    """

    expected_points: list[str]


class InterviewPlan(BaseModel):
    """Plan de entrevista validado y listo para guardar en la sesión.

    Attributes:
        role_title: Título del puesto al que se orienta la entrevista.
        focus_areas: Áreas de conocimiento en las que se enfoca.
        questions: Preguntas que se mostrarán a la persona candidata.
    """

    role_title: str
    focus_areas: list[str]
    questions: list[InterviewQuestion]


class InterviewPlanInternal(BaseModel):
    """Plan de entrevista con preguntas internas que incluyen puntos esperados.

    Attributes:
        role_title: Título del puesto al que se orienta la entrevista.
        focus_areas: Áreas de conocimiento en las que se enfoca.
        questions: Preguntas internas con sus puntos esperados.
    """

    role_title: str
    focus_areas: list[str]
    questions: list[InterviewQuestionInternal]


class CriterionScore(BaseModel):
    """Puntuación de un criterio concreto dentro de una evaluación.

    Attributes:
        criterion: Criterio evaluado.
        score: Puntuación del criterio, entre 0 y 5.
        comment: Justificación breve de la puntuación.
    """

    criterion: Criterion
    score: int = Field(ge=0, le=5)
    comment: str = Field(min_length=1, max_length=300)


class AnswerEvaluation(BaseModel):
    """Evaluación estructurada de una respuesta generada por el LLM.

    Attributes:
        criteria: Puntuación de los cinco criterios de evaluación.
        strengths: Puntos fuertes de la respuesta.
        gaps: Carencias o aspectos a mejorar.
        unsupported_claims: Afirmaciones sin respaldo en el perfil.
        suggested_outline: Esquema sugerido para mejorar la respuesta.
        evidence_ids: IDs de evidencias del perfil citadas.
    """

    criteria: list[CriterionScore] = Field(min_length=5, max_length=5)
    strengths: list[str] = Field(min_length=1, max_length=3)
    gaps: list[str] = Field(min_length=1, max_length=3)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=5)
    suggested_outline: list[str] = Field(min_length=3, max_length=5)
    evidence_ids: list[str] = []


class SuggestedAnswer(BaseModel):
    """Borrador de respuesta sugerida para una pregunta pendiente.

    Attributes:
        answer: Texto propuesto, entre 40 y 4000 caracteres.
        outline: Esquema de la respuesta, entre 3 y 6 puntos.
        evidence_ids: IDs de evidencias del perfil citadas.
        caveats: Advertencias sobre afirmaciones que requieren cuidado.
        preparation_notes: Notas de preparación para la persona candidata.
    """

    answer: str = Field(min_length=40, max_length=4000)
    outline: list[str] = Field(min_length=3, max_length=6)
    evidence_ids: list[str] = []
    caveats: list[str] = Field(default_factory=list, max_length=5)
    preparation_notes: list[str] = Field(default_factory=list, max_length=5)


class InterviewStartRequest(BaseModel):
    """Petición para crear una sesión de entrevista.

    Attributes:
        job_text: Texto de la oferta de empleo.
        output_type: Tipo de documento generado previamente.
        document: Documento validado que sirve de contexto.
        evidence: Evidencias del perfil, entre 1 y 20.
        question_count: Número de preguntas deseadas, entre 5 y 10.
    """

    job_text: str = Field(min_length=50, max_length=8000)
    output_type: OutputType
    document: dict[str, Any]
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    question_count: int = Field(
        default_factory=lambda: settings.interview_question_count, ge=5, le=10
    )


class InterviewStartResponse(BaseModel):
    """Datos de una sesión de entrevista recién creada.

    Attributes:
        session_id: Identificador único de la sesión.
        expires_at: Fecha y hora en que caduca la sesión.
        role_title: Título del puesto al que se orienta la entrevista.
        focus_areas: Áreas de conocimiento en las que se enfoca.
        questions: Preguntas de la entrevista.
    """

    session_id: str
    expires_at: datetime
    role_title: str
    focus_areas: list[str]
    questions: list[InterviewQuestion]


class InterviewAnswerRequest(BaseModel):
    """Petición para evaluar la respuesta a una pregunta.

    Attributes:
        question_id: Identificador de la pregunta respondida.
        answer: Texto de la respuesta; su longitud se valida en el servicio.
    """

    question_id: str = Field(min_length=1, max_length=20)
    # La longitud se valida en el servicio para no devolver el texto en errores 422.
    answer: str


class InterviewAnswerResponse(BaseModel):
    """Resultado de evaluar una respuesta de la entrevista.

    Attributes:
        question_id: Identificador de la pregunta respondida.
        score: Puntuación global, entre 0 y 100.
        verdict: Valoración global de la respuesta.
        criteria: Puntuación detallada por criterio.
        strengths: Puntos fuertes detectados.
        gaps: Carencias o aspectos a mejorar.
        unsupported_claims: Afirmaciones sin respaldo en el perfil.
        suggested_outline: Esquema sugerido para mejorar la respuesta.
        evidence: Evidencias del perfil relacionadas con la respuesta.
        answered: Número de preguntas respondidas hasta ahora.
        total: Número total de preguntas de la sesión.
        next_question_id: Siguiente pregunta pendiente, si existe.
    """

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
    """Respuesta sugerida para una pregunta todavía pendiente.

    Attributes:
        question_id: Identificador de la pregunta.
        answer: Texto del borrador de respuesta.
        outline: Esquema de la respuesta.
        caveats: Advertencias sobre afirmaciones que requieren cuidado.
        preparation_notes: Notas de preparación para la persona candidata.
        evidence: Evidencias del perfil citadas en el borrador.
    """

    question_id: str
    answer: str
    outline: list[str]
    caveats: list[str]
    preparation_notes: list[str]
    evidence: list[Evidence]


class InterviewSummary(BaseModel):
    """Resumen determinista del progreso de una sesión de entrevista.

    Attributes:
        answered: Número de preguntas respondidas.
        total: Número total de preguntas de la sesión.
        completed: Indica si se respondieron todas las preguntas.
        overall_score: Puntuación media global, entre 0 y 100.
        by_category: Puntuación media por categoría.
        top_strengths: Puntos fuertes más repetidos.
        top_gaps: Carencias más repetidas.
        next_steps: Recomendaciones de preparación.
        unanswered_question_ids: IDs de las preguntas sin responder.
    """

    answered: int
    total: int
    completed: bool
    overall_score: int
    by_category: dict[str, int]
    top_strengths: list[str]
    top_gaps: list[str]
    next_steps: list[str]
    unanswered_question_ids: list[str]
