import logging
import time
from collections import Counter
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agent.context import collect_evidence_ids, format_evidence
from app.agent.state import OUTPUT_SCHEMAS, OutputType
from app.core.config import settings
from app.interview.prompts import (
    EVAL_SYSTEM,
    PLAN_SYSTEM,
    build_evaluation_prompt,
    build_plan_prompt,
    evaluation_feedback,
    plan_feedback,
)
from app.interview.store import (
    AlreadyAnswered,
    AnswerRecord,
    InterviewSession,
    InterviewSessionStore,
    utcnow,
)
from app.schemas.evidence import Evidence
from app.schemas.interview import (
    AnswerEvaluation,
    Criterion,
    GeneratedInterviewPlan,
    InterviewAnswerResponse,
    InterviewCategory,
    InterviewPlan,
    InterviewPlanInternal,
    InterviewQuestion,
    InterviewQuestionInternal,
    InterviewStartRequest,
    InterviewStartResponse,
    InterviewSummary,
    Verdict,
)

logger = logging.getLogger("interview")

MAX_PLAN_ATTEMPTS = 2
MAX_EVALUATION_ATTEMPTS = 2
MIN_QUESTIONS = 5
MAX_QUESTIONS = 10
MIN_ANSWER_CHARS = 1

TECHNICAL_CATEGORIES = {InterviewCategory.technical, InterviewCategory.project}
REQUIRED_CATEGORIES = (
    InterviewCategory.technical,
    InterviewCategory.project,
    InterviewCategory.behavioral,
)

TECHNICAL_WEIGHTS = {
    Criterion.relevance: 0.25,
    Criterion.evidence_alignment: 0.25,
    Criterion.specificity: 0.20,
    Criterion.structure: 0.10,
    Criterion.technical_depth: 0.20,
}
GENERAL_WEIGHTS = {
    Criterion.relevance: 0.25,
    Criterion.evidence_alignment: 0.25,
    Criterion.specificity: 0.20,
    Criterion.structure: 0.20,
    Criterion.technical_depth: 0.10,
}

NEXT_STEP_BY_CATEGORY = {
    InterviewCategory.technical: (
        "Repasa los fundamentos técnicos de la oferta y prepara ejemplos con trade-offs."
    ),
    InterviewCategory.project: (
        "Prepara historias de proyecto con contexto, decisiones y métricas de impacto."
    ),
    InterviewCategory.behavioral: (
        "Practica respuestas con estructura STAR y cierra con el aprendizaje."
    ),
    InterviewCategory.role_fit: (
        "Conecta tu motivación y experiencia con los requisitos clave de la vacante."
    ),
    InterviewCategory.gap: (
        "Prepara un plan de aprendizaje para las carencias y ejemplos de cómo las has cubierto."
    ),
}
GENERAL_NEXT_STEP = (
    "Revisa las evidencias citadas y evita afirmar experiencia que no esté respaldada por tu perfil."
)


class InvalidInterviewInput(Exception):
    """Entrada inválida para la entrevista (se traduce a 422)."""


class InterviewGenerationFailed(Exception):
    """El LLM no produjo un plan o una evaluación válidos (se traduce a 502)."""


def validate_document(body: InterviewStartRequest) -> tuple[BaseModel, dict[str, Any]]:
    schema = OUTPUT_SCHEMAS[body.output_type]
    try:
        parsed = schema.model_validate(body.document)
    except ValidationError as exc:
        raise InvalidInterviewInput(
            "El documento no es válido para el tipo de salida."
        ) from exc
    return parsed, parsed.model_dump(mode="json")


def select_session_evidence(
    document: BaseModel, evidence: list[Evidence], max_evidence: int
) -> list[Evidence]:
    valid = {e.id: e for e in evidence}
    cited = collect_evidence_ids(document)
    unknown = cited - valid.keys()
    if unknown:
        raise InvalidInterviewInput(
            "El documento cita evidencias que no están en el material."
        )
    unique: dict[str, Evidence] = {}
    for e in evidence:
        if e.id not in unique or e.score > unique[e.id].score:
            unique[e.id] = e
    ordered = sorted(unique.values(), key=lambda e: e.score, reverse=True)
    selected_ids = set(cited)
    for e in ordered:
        if len(selected_ids) >= max_evidence:
            break
        selected_ids.add(e.id)
    return [e for e in ordered if e.id in selected_ids]


def _plan_error(
    plan: GeneratedInterviewPlan, question_count: int, valid_ids: set[str]
) -> str | None:
    if len(plan.questions) != question_count:
        return (
            f"Deben ser exactamente {question_count} preguntas y llegaron "
            f"{len(plan.questions)}."
        )
    categories = {q.category for q in plan.questions}
    missing = [c.value for c in REQUIRED_CATEGORIES if c not in categories]
    if missing:
        return f"Faltan categorías obligatorias: {', '.join(missing)}."
    if not ({InterviewCategory.role_fit, InterviewCategory.gap} & categories):
        return "Falta al menos una pregunta de categoría role_fit o gap."
    for i, q in enumerate(plan.questions, 1):
        unknown = set(q.evidence_ids) - valid_ids
        if unknown:
            return (
                f"La pregunta {i} cita evidence_ids inexistentes: {sorted(unknown)}. "
                f"Usa solo: {sorted(valid_ids)}"
            )
        if q.category in TECHNICAL_CATEGORIES and not q.evidence_ids:
            return (
                f"La pregunta {i} es {q.category.value} y debe citar al menos "
                "un evidence_id."
            )
    return None


def generate_plan(
    llm,
    *,
    job_text: str,
    output_type: OutputType,
    document: dict[str, Any],
    evidence: list[Evidence],
    question_count: int,
) -> InterviewPlanInternal:
    prompt = build_plan_prompt(
        job_text=job_text,
        output_type=output_type,
        document=document,
        evidence_text=format_evidence(evidence),
        question_count=question_count,
    )
    valid_ids = {e.id for e in evidence}
    last_error = ""
    for attempt in range(1, MAX_PLAN_ATTEMPTS + 1):
        human = prompt
        if last_error:
            human += f"\n\n{plan_feedback(last_error)}"
        try:
            plan = llm.with_structured_output(GeneratedInterviewPlan).invoke(
                [("system", PLAN_SYSTEM), ("human", human)]
            )
        except ValidationError as exc:
            last_error = f"salida con formato inválido: {exc.errors()[:3]}"
            continue
        error = _plan_error(plan, question_count, valid_ids)
        if error is None:
            questions = [
                InterviewQuestionInternal(
                    id=f"q{i}",
                    category=q.category,
                    difficulty=q.difficulty,
                    question=q.question.strip(),
                    why_asked=q.why_asked.strip(),
                    evidence_ids=list(dict.fromkeys(q.evidence_ids)),
                    expected_points=q.expected_points,
                )
                for i, q in enumerate(plan.questions, 1)
            ]
            return InterviewPlanInternal(
                role_title=plan.role_title.strip(),
                focus_areas=plan.focus_areas,
                questions=questions,
            )
        last_error = error
    logger.warning(
        "interview plan generation failed",
        extra={"attempts": MAX_PLAN_ATTEMPTS, "error": last_error[:300]},
    )
    raise InterviewGenerationFailed(
        "No se pudo generar un plan de entrevista válido. Inténtalo de nuevo."
    )


def _evaluation_error(evaluation: AnswerEvaluation, valid_ids: set[str]) -> str | None:
    criteria = [c.criterion for c in evaluation.criteria]
    if len(criteria) != 5 or set(criteria) != set(Criterion):
        return (
            "Deben aparecer exactamente los cinco criterios, una vez cada uno: "
            f"{', '.join(c.value for c in Criterion)}."
        )
    unknown = set(evaluation.evidence_ids) - valid_ids
    if unknown:
        return (
            f"Estos evidence_ids no existen: {sorted(unknown)}. "
            f"Usa solo: {sorted(valid_ids)}"
        )
    return None


def evaluate_with_llm(
    session: InterviewSession,
    question: InterviewQuestionInternal,
    answer: str,
) -> AnswerEvaluation:
    prompt = build_evaluation_prompt(
        job_text=session.job_text,
        output_type=session.output_type,
        document=session.document,
        evidence_text=format_evidence(session.evidence),
        question=question.question,
        category=question.category.value,
        difficulty=question.difficulty.value,
        expected_points=question.expected_points,
        answer=answer,
    )
    valid_ids = {e.id for e in session.evidence}
    last_error = ""
    for attempt in range(1, MAX_EVALUATION_ATTEMPTS + 1):
        human = prompt
        if last_error:
            human += f"\n\n{evaluation_feedback(last_error)}"
        try:
            evaluation = session.llm.with_structured_output(AnswerEvaluation).invoke(
                [("system", EVAL_SYSTEM), ("human", human)]
            )
        except ValidationError as exc:
            last_error = f"salida con formato inválido: {exc.errors()[:3]}"
            continue
        error = _evaluation_error(evaluation, valid_ids)
        if error is None:
            return evaluation
        last_error = error
    logger.warning(
        "interview evaluation failed",
        extra={"attempts": MAX_EVALUATION_ATTEMPTS, "error": last_error[:300]},
    )
    raise InterviewGenerationFailed(
        "No se pudo evaluar la respuesta. Inténtalo de nuevo."
    )


def score_answer(evaluation: AnswerEvaluation, category: InterviewCategory) -> int:
    weights = TECHNICAL_WEIGHTS if category in TECHNICAL_CATEGORIES else GENERAL_WEIGHTS
    total = sum(weights[c.criterion] * c.score for c in evaluation.criteria)
    return max(0, min(100, round(20 * total)))


def verdict_for(score: int) -> Verdict:
    if score >= 80:
        return Verdict.strong
    if score >= 60:
        return Verdict.partial
    return Verdict.weak


def public_plan(plan: InterviewPlanInternal) -> InterviewPlan:
    return InterviewPlan(
        role_title=plan.role_title,
        focus_areas=plan.focus_areas,
        questions=[
            InterviewQuestion(**q.model_dump(exclude={"expected_points"}))
            for q in plan.questions
        ],
    )


def start_interview(
    store: InterviewSessionStore,
    llm,
    body: InterviewStartRequest,
    model_id: str | None = None,
) -> InterviewSession:
    if not MIN_QUESTIONS <= body.question_count <= MAX_QUESTIONS:
        raise InvalidInterviewInput(
            f"El número de preguntas debe estar entre {MIN_QUESTIONS} y {MAX_QUESTIONS}."
        )
    parsed, document = validate_document(body)
    evidence = select_session_evidence(
        parsed, body.evidence, settings.interview_max_evidence
    )
    plan = generate_plan(
        llm,
        job_text=body.job_text,
        output_type=body.output_type,
        document=document,
        evidence=evidence,
        question_count=body.question_count,
    )
    session = store.create(
        job_text=body.job_text,
        output_type=body.output_type,
        document=document,
        evidence=evidence,
        plan=plan,
        llm=llm,
        model_id=model_id,
    )
    logger.info(
        "interview session created",
        extra={
            "session_id": session.id[:8],
            "questions": len(plan.questions),
            "model": model_id or "default",
            "evidence": len(evidence),
        },
    )
    return session


def start_response(session: InterviewSession) -> InterviewStartResponse:
    plan = public_plan(session.plan)
    return InterviewStartResponse(
        session_id=session.id,
        expires_at=session.expires_at,
        role_title=plan.role_title,
        focus_areas=plan.focus_areas,
        questions=plan.questions,
    )


def _next_question_id(session: InterviewSession, answered: dict[str, AnswerRecord]) -> str | None:
    for q in session.plan.questions:
        if q.id not in answered:
            return q.id
    return None


def submit_answer(
    store: InterviewSessionStore,
    session_id: str,
    question_id: str,
    answer: str,
) -> InterviewAnswerResponse:
    if len(answer.strip()) < MIN_ANSWER_CHARS:
        raise InvalidInterviewInput("La respuesta no puede estar vacía.")
    max_chars = settings.interview_max_answer_chars
    if len(answer) > max_chars:
        raise InvalidInterviewInput(
            f"La respuesta supera el máximo de {max_chars} caracteres."
        )

    session = store.get(session_id)
    question = next((q for q in session.plan.questions if q.id == question_id), None)
    if question is None:
        raise InvalidInterviewInput("La pregunta no pertenece a esta sesión.")
    if question_id in store.snapshot_answers(session_id):
        raise AlreadyAnswered

    started = time.perf_counter()
    evaluation = evaluate_with_llm(session, question, answer.strip())
    score = score_answer(evaluation, question.category)
    verdict = verdict_for(score)
    record = AnswerRecord(
        evaluation=evaluation,
        score=score,
        verdict=verdict,
        answered_at=utcnow(),
    )
    store.record_answer(session_id, question_id, record)
    answered = store.snapshot_answers(session_id)

    logger.info(
        "interview answer evaluated",
        extra={
            "session_id": session_id[:8],
            "question_id": question_id,
            "score": score,
            "verdict": verdict.value,
            "ms": round((time.perf_counter() - started) * 1000),
        },
    )

    cited = set(evaluation.evidence_ids)
    return InterviewAnswerResponse(
        question_id=question_id,
        score=score,
        verdict=verdict,
        criteria=evaluation.criteria,
        strengths=evaluation.strengths,
        gaps=evaluation.gaps,
        unsupported_claims=evaluation.unsupported_claims,
        suggested_outline=evaluation.suggested_outline,
        evidence=[e for e in session.evidence if e.id in cited],
        answered=len(answered),
        total=len(session.plan.questions),
        next_question_id=_next_question_id(session, answered),
    )


def _top_strings(
    records: list[AnswerRecord],
    field: str,
    fallback: AnswerRecord | None,
    limit: int = 3,
) -> list[str]:
    counter = Counter(
        item for r in records for item in getattr(r.evaluation, field)
    )
    picked: list[str] = []
    for item, _ in counter.most_common():
        if item not in picked:
            picked.append(item)
        if len(picked) == limit:
            break
    if len(picked) < limit and fallback is not None:
        for item in getattr(fallback.evaluation, field):
            if item not in picked:
                picked.append(item)
            if len(picked) == limit:
                break
    return picked[:limit]


def build_summary(
    store: InterviewSessionStore, session_id: str
) -> InterviewSummary:
    session = store.get(session_id)
    answers = store.snapshot_answers(session_id)
    if not answers:
        raise InvalidInterviewInput(
            "Responde al menos una pregunta antes de pedir el resumen."
        )

    by_question_category = {q.id: q.category for q in session.plan.questions}
    category_scores: dict[InterviewCategory, list[int]] = {}
    for qid, record in answers.items():
        category = by_question_category.get(qid)
        if category is not None:
            category_scores.setdefault(category, []).append(record.score)

    by_category = {
        category.value: round(sum(values) / len(values))
        for category in InterviewCategory
        if (values := category_scores.get(category))
    }
    overall = round(sum(r.score for r in answers.values()) / len(answers))
    records = list(answers.values())
    best = max(records, key=lambda r: r.score)
    worst = min(records, key=lambda r: r.score)

    low_categories = sorted(
        ((category, score) for category, score in by_category.items() if score < 70),
        key=lambda item: item[1],
    )
    next_steps = [
        NEXT_STEP_BY_CATEGORY[InterviewCategory(category)]
        for category, _ in low_categories[:2]
    ]
    next_steps.append(GENERAL_NEXT_STEP)

    return InterviewSummary(
        answered=len(answers),
        total=len(session.plan.questions),
        completed=len(answers) == len(session.plan.questions),
        overall_score=overall,
        by_category=by_category,
        top_strengths=_top_strings(records, "strengths", best),
        top_gaps=_top_strings(records, "gaps", worst),
        next_steps=next_steps,
        unanswered_question_ids=[
            q.id for q in session.plan.questions if q.id not in answers
        ],
    )


def delete_session(store: InterviewSessionStore, session_id: str) -> None:
    store.delete(session_id)
