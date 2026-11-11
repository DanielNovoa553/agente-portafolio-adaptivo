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
    SUGGEST_SYSTEM,
    build_evaluation_prompt,
    build_plan_prompt,
    build_suggestion_prompt,
    evaluation_feedback,
    plan_feedback,
    suggestion_feedback,
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
    InterviewSuggestionResponse,
    InterviewSummary,
    SuggestedAnswer,
    Verdict,
)

logger = logging.getLogger("interview")

MAX_PLAN_ATTEMPTS = 2
MAX_EVALUATION_ATTEMPTS = 2
MAX_SUGGESTION_ATTEMPTS = 2
MIN_QUESTIONS = 5
MAX_QUESTIONS = 10
MIN_ANSWER_CHARS = 1
MIN_SUGGESTION_CHARS = 40

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
    """Valida el documento de la petición contra el esquema de su tipo.

    Args:
        body: Petición de inicio con el tipo de salida y el documento.

    Returns:
        Tupla con el documento validado y su versión serializada en JSON.

    Raises:
        InvalidInterviewInput: Si el documento no cumple el esquema esperado.
    """
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
    """Selecciona las evidencias de la sesión a partir del documento.

    Verifica que el documento solo cite IDs existentes y combina las evidencias
    citadas con las mejor puntuadas hasta `max_evidence`.

    Args:
        document: Documento generado que cita evidencias.
        evidence: Evidencias disponibles para la oferta.
        max_evidence: Número máximo de evidencias a conservar.

    Returns:
        Evidencias únicas ordenadas de mayor a menor score.

    Raises:
        InvalidInterviewInput: Si el documento cita IDs que no existen.
    """
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
    """Valida el plan generado y devuelve el primer error encontrado.

    Comprueba el número exacto de preguntas, las categorías obligatorias, los
    `evidence_ids` existentes y que las preguntas técnicas o de proyecto citen
    al menos una evidencia.

    Args:
        plan: Plan generado por el modelo.
        question_count: Número exacto de preguntas esperado.
        valid_ids: IDs de evidencia disponibles en la sesión.

    Returns:
        Mensaje de error, o `None` si el plan es válido.
    """
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
    """Genera y valida el plan de entrevista con hasta dos intentos.

    Cada reintento añade el error anterior al prompt. Un plan válido debe tener
    exactamente `question_count` preguntas, cubrir las categorías obligatorias
    y citar solo evidencias existentes.

    Args:
        llm: Modelo de lenguaje con salida estructurada.
        job_text: Texto completo de la oferta.
        output_type: Tipo de documento generado que se usa como contexto.
        document: Documento generado serializado como diccionario.
        evidence: Evidencias seleccionadas para la sesión.
        question_count: Número exacto de preguntas solicitadas.

    Returns:
        Plan interno con preguntas numeradas y puntos esperados.

    Raises:
        InterviewGenerationFailed: Si no se obtiene un plan válido tras
            `MAX_PLAN_ATTEMPTS` intentos.
    """
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
    """Valida la evaluación generada y devuelve el primer error encontrado.

    Comprueba que aparezcan los cinco criterios exactos y que solo se citen
    evidencias existentes.

    Args:
        evaluation: Evaluación devuelta por el modelo.
        valid_ids: IDs de evidencia disponibles en la sesión.

    Returns:
        Mensaje de error, o `None` si la evaluación es válida.
    """
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
    """Evalúa una respuesta con el modelo de la sesión y hasta dos intentos.

    Cada reintento añade el error anterior al prompt. La evaluación debe incluir
    los cinco criterios exactos y citar solo evidencias existentes.

    Args:
        session: Sesión con oferta, documento, evidencias y modelo fijado.
        question: Pregunta que se está respondiendo.
        answer: Texto de la respuesta, ya sin espacios sobrantes.

    Returns:
        Evaluación estructurada de la respuesta.

    Raises:
        InterviewGenerationFailed: Si no se obtiene una evaluación válida tras
            `MAX_EVALUATION_ATTEMPTS` intentos.
    """
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


def _suggestion_error(
    suggestion: SuggestedAnswer,
    question: InterviewQuestionInternal,
    valid_ids: set[str],
) -> str | None:
    """Valida el borrador de respuesta y devuelve el primer error encontrado.

    Comprueba la longitud mínima, que solo se citen evidencias existentes y que
    las preguntas técnicas o de proyecto citen al menos una.

    Args:
        suggestion: Borrador devuelto por el modelo.
        question: Pregunta para la que se generó el borrador.
        valid_ids: IDs de evidencia disponibles en la sesión.

    Returns:
        Mensaje de error, o `None` si el borrador es válido.
    """
    if len(suggestion.answer.strip()) < MIN_SUGGESTION_CHARS:
        return f"El borrador debe tener al menos {MIN_SUGGESTION_CHARS} caracteres."
    unknown = set(suggestion.evidence_ids) - valid_ids
    if unknown:
        return (
            f"El borrador cita evidence_ids inexistentes: {sorted(unknown)}. "
            f"Usa solo: {sorted(valid_ids)}"
        )
    if question.category in TECHNICAL_CATEGORIES and not suggestion.evidence_ids:
        return (
            f"La pregunta es {question.category.value} y el borrador debe citar "
            "al menos un evidence_id."
        )
    return None


def generate_suggested_answer(
    session: InterviewSession,
    question: InterviewQuestionInternal,
) -> SuggestedAnswer:
    """Genera un borrador de respuesta con hasta dos intentos.

    El borrador debe tener al menos `MIN_SUGGESTION_CHARS` caracteres, citar
    solo evidencias existentes y, en preguntas técnicas o de proyecto, citar al
    menos una. Se devuelve con el texto recortado y los IDs sin duplicados.

    Args:
        session: Sesión con oferta, documento, evidencias y modelo fijado.
        question: Pregunta para la que se genera el borrador.

    Returns:
        Borrador validado y normalizado.

    Raises:
        InterviewGenerationFailed: Si no se obtiene un borrador válido tras
            `MAX_SUGGESTION_ATTEMPTS` intentos.
    """
    prompt = build_suggestion_prompt(
        job_text=session.job_text,
        output_type=session.output_type,
        document=session.document,
        evidence_text=format_evidence(session.evidence),
        question=question.question,
        category=question.category.value,
        difficulty=question.difficulty.value,
        expected_points=question.expected_points,
    )
    valid_ids = {e.id for e in session.evidence}
    last_error = ""
    for attempt in range(1, MAX_SUGGESTION_ATTEMPTS + 1):
        human = prompt
        if last_error:
            human += f"\n\n{suggestion_feedback(last_error)}"
        try:
            suggestion = session.llm.with_structured_output(SuggestedAnswer).invoke(
                [("system", SUGGEST_SYSTEM), ("human", human)]
            )
        except ValidationError as exc:
            last_error = f"salida con formato inválido: {exc.errors()[:3]}"
            continue
        error = _suggestion_error(suggestion, question, valid_ids)
        if error is None:
            return suggestion.model_copy(
                update={
                    "answer": suggestion.answer.strip(),
                    "evidence_ids": list(dict.fromkeys(suggestion.evidence_ids)),
                }
            )
        last_error = error
    logger.warning(
        "interview suggestion generation failed",
        extra={"attempts": MAX_SUGGESTION_ATTEMPTS, "error": last_error[:300]},
    )
    raise InterviewGenerationFailed(
        "No se pudo generar un borrador de respuesta. Inténtalo de nuevo."
    )


def score_answer(evaluation: AnswerEvaluation, category: InterviewCategory) -> int:
    """Calcula la nota de 0 a 100 a partir de los cinco criterios.

    Usa pesos específicos para preguntas técnicas o de proyecto y otros más
    generales para el resto.

    Args:
        evaluation: Evaluación con las notas de cada criterio.
        category: Categoría de la pregunta evaluada.

    Returns:
        Puntuación entera acotada al rango 0-100.
    """
    weights = TECHNICAL_WEIGHTS if category in TECHNICAL_CATEGORIES else GENERAL_WEIGHTS
    total = sum(weights[c.criterion] * c.score for c in evaluation.criteria)
    return max(0, min(100, round(20 * total)))


def verdict_for(score: int) -> Verdict:
    """Clasifica una puntuación en un veredicto cualitativo.

    Args:
        score: Puntuación de 0 a 100.

    Returns:
        `strong` desde 80, `partial` desde 60 y `weak` por debajo de 60.
    """
    if score >= 80:
        return Verdict.strong
    if score >= 60:
        return Verdict.partial
    return Verdict.weak


def public_plan(plan: InterviewPlanInternal) -> InterviewPlan:
    """Convierte el plan interno en la versión pública para el cliente.

    Args:
        plan: Plan interno con puntos esperados y metadatos.

    Returns:
        Plan sin `expected_points`, apto para enviar en la respuesta.
    """
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
    """Crea una sesión de entrevista a partir de la petición.

    Valida el rango de preguntas, el documento y las evidencias citadas, genera
    el plan con el modelo indicado y guarda la sesión en el almacén.

    Args:
        store: Almacén de sesiones de entrevista.
        llm: Modelo de lenguaje con salida estructurada.
        body: Petición de inicio con oferta, documento y evidencias.
        model_id: Identificador del modelo elegido, o `None` para el modelo por defecto.

    Returns:
        Sesión creada y registrada en el almacén.

    Raises:
        InvalidInterviewInput: Si el número de preguntas, el documento o las
            evidencias no son válidos.
        InterviewGenerationFailed: Si no se puede generar un plan válido.
    """
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
    """Construye la respuesta pública de inicio de entrevista.

    Args:
        session: Sesión recién creada.

    Returns:
        Respuesta con ID, caducidad, título, áreas y preguntas públicas.
    """
    plan = public_plan(session.plan)
    return InterviewStartResponse(
        session_id=session.id,
        expires_at=session.expires_at,
        role_title=plan.role_title,
        focus_areas=plan.focus_areas,
        questions=plan.questions,
    )


def _next_question_id(session: InterviewSession, answered: dict[str, AnswerRecord]) -> str | None:
    """Busca la primera pregunta de la sesión que aún no tiene respuesta.

    Args:
        session: Sesión con el plan de preguntas.
        answered: Respuestas registradas, indexadas por ID de pregunta.

    Returns:
        ID de la siguiente pregunta pendiente, o `None` si están todas.
    """
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
    """Evalúa y registra la respuesta a una pregunta de la sesión.

    La respuesta no se guarda ni se registra en logs; solo se conservan la
    evaluación, la nota, el veredicto y el momento de la respuesta.

    Args:
        store: Almacén de sesiones de entrevista.
        session_id: Identificador de la sesión.
        question_id: Identificador de la pregunta respondida.
        answer: Texto de la respuesta enviada por la persona candidata.

    Returns:
        Respuesta con nota, veredicto, feedback, evidencias y progreso.

    Raises:
        InvalidInterviewInput: Si la respuesta está vacía, supera el máximo de
            caracteres o la pregunta no pertenece a la sesión.
        AlreadyAnswered: Si la pregunta ya tiene una respuesta registrada.
        SessionNotFound: Si la sesión no existe o ha expirado.
        InterviewGenerationFailed: Si no se puede evaluar la respuesta.
    """
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


def suggest_answer(
    store: InterviewSessionStore,
    session_id: str,
    question_id: str,
) -> InterviewSuggestionResponse:
    """Devuelve un borrador de respuesta para una pregunta pendiente.

    Reutiliza el borrador cacheado en la sesión si existe; si no, lo genera y
    lo guarda. El texto del borrador no se guarda en disco ni se registra en
    logs.

    Args:
        store: Almacén de sesiones de entrevista.
        session_id: Identificador de la sesión.
        question_id: Identificador de la pregunta pendiente.

    Returns:
        Borrador con respuesta, esquema, avisos, notas y evidencias citadas.

    Raises:
        InvalidInterviewInput: Si la pregunta no pertenece a la sesión.
        AlreadyAnswered: Si la pregunta ya fue respondida.
        SessionNotFound: Si la sesión no existe o ha expirado.
        InterviewGenerationFailed: Si no se puede generar un borrador válido.
    """
    session = store.get(session_id)
    question = next((q for q in session.plan.questions if q.id == question_id), None)
    if question is None:
        raise InvalidInterviewInput("La pregunta no pertenece a esta sesión.")
    if question_id in store.snapshot_answers(session_id):
        raise AlreadyAnswered

    started = time.perf_counter()
    suggestion = store.get_suggestion(session_id, question_id)
    cached = suggestion is not None
    if suggestion is None:
        suggestion = generate_suggested_answer(session, question)
        store.record_suggestion(session_id, question_id, suggestion)

    logger.info(
        "interview suggestion served",
        extra={
            "session_id": session_id[:8],
            "question_id": question_id,
            "cached": cached,
            "evidence": len(suggestion.evidence_ids),
            "ms": round((time.perf_counter() - started) * 1000),
        },
    )

    cited = set(suggestion.evidence_ids)
    return InterviewSuggestionResponse(
        question_id=question_id,
        answer=suggestion.answer,
        outline=suggestion.outline,
        caveats=suggestion.caveats,
        preparation_notes=suggestion.preparation_notes,
        evidence=[e for e in session.evidence if e.id in cited],
    )


def _top_strings(
    records: list[AnswerRecord],
    field: str,
    fallback: AnswerRecord | None,
    limit: int = 3,
) -> list[str]:
    """Extrae los valores más repetidos de un campo de las evaluaciones.

    Args:
        records: Respuestas registradas en la sesión.
        field: Nombre del campo de `AnswerEvaluation` (`strengths` o `gaps`).
        fallback: Evaluación de la que completar la lista si falta variedad.
        limit: Número máximo de valores a devolver.

    Returns:
        Hasta `limit` valores únicos, ordenados por frecuencia.
    """
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
    """Resume el progreso y los resultados de una sesión de entrevista.

    Calcula la media global y por categoría, extrae fortalezas y carencias
    recurrentes y propone pasos siguientes según las categorías más bajas.

    Args:
        store: Almacén de sesiones de entrevista.
        session_id: Identificador de la sesión.

    Returns:
        Resumen con progreso, notas, fortalezas, carencias y pasos siguientes.

    Raises:
        InvalidInterviewInput: Si aún no hay ninguna respuesta registrada.
        SessionNotFound: Si la sesión no existe o ha expirado.
    """
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
    """Elimina una sesión del almacén.

    Args:
        store: Almacén de sesiones de entrevista.
        session_id: Identificador de la sesión que se quiere borrar.

    Raises:
        SessionNotFound: Si la sesión no existe o ha expirado.
    """
    store.delete(session_id)
