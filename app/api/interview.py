from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.deps import consent_dep, interview_store_dep, llm_dep
from app.core.config import settings
from app.core.limiter import limiter
from app.interview.service import (
    InvalidInterviewInput,
    InterviewGenerationFailed,
    build_summary,
    delete_session,
    start_interview,
    start_response,
    submit_answer,
    suggest_answer,
)
from app.interview.store import AlreadyAnswered, InterviewSessionStore, SessionNotFound
from app.schemas.interview import (
    InterviewAnswerRequest,
    InterviewAnswerResponse,
    InterviewStartRequest,
    InterviewStartResponse,
    InterviewSuggestionResponse,
    InterviewSummary,
)

router = APIRouter(tags=["interview"], dependencies=[Depends(consent_dep)])

SESSION_NOT_FOUND = "Sesión de entrevista no encontrada o expirada."


@router.post(
    "/interview/sessions",
    response_model=InterviewStartResponse,
    status_code=201,
)
@limiter.limit(settings.interview_start_rate_limit)
def create_session(
    request: Request,
    body: InterviewStartRequest,
    store: InterviewSessionStore = Depends(interview_store_dep),
    llm=Depends(llm_dep),
):
    """Crea una sesión de entrevista a partir de la oferta y el documento.

    Args:
        request: Petición HTTP; también aporta el parámetro de query `model`.
        body: Oferta, documento, evidencias y número de preguntas.
        store: Almacén en memoria de sesiones de entrevista.
        llm: Cliente LLM con el que se genera el plan de preguntas.

    Returns:
        InterviewStartResponse: Identificador, caducidad y preguntas de la sesión.

    Raises:
        HTTPException: 403 sin consentimiento, 422 con entrada inválida o modelo no
            disponible, 429 si se supera el límite, 502 si no se genera un plan
            válido y 503 si falta la API key o el LLM no responde.
    """
    try:
        session = start_interview(
            store, llm, body, model_id=request.query_params.get("model")
        )
    except InvalidInterviewInput as exc:
        raise HTTPException(422, str(exc)) from exc
    except InterviewGenerationFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    return start_response(session)


@router.post(
    "/interview/sessions/{session_id}/answers",
    response_model=InterviewAnswerResponse,
)
@limiter.limit(settings.interview_answer_rate_limit)
def answer_question(
    request: Request,
    session_id: str,
    body: InterviewAnswerRequest,
    store: InterviewSessionStore = Depends(interview_store_dep),
):
    """Evalúa la respuesta a una pregunta de la sesión.

    Args:
        request: Petición HTTP, requerida por el limitador de tasa.
        session_id: Identificador de la sesión de entrevista.
        body: Pregunta respondida y texto de la respuesta.
        store: Almacén en memoria de sesiones de entrevista.

    Returns:
        InterviewAnswerResponse: Puntuación, veredicto, feedback y evidencia citada.

    Raises:
        HTTPException: 403 sin consentimiento, 404 si la sesión no existe o expiró,
            409 si la pregunta ya fue respondida, 422 con entrada inválida, 429 si
            se supera el límite, 502 si falla la evaluación y 503 si el LLM no
            responde.
    """
    try:
        return submit_answer(store, session_id, body.question_id, body.answer)
    except SessionNotFound as exc:
        raise HTTPException(404, SESSION_NOT_FOUND) from exc
    except AlreadyAnswered as exc:
        raise HTTPException(409, "Ya respondiste esta pregunta.") from exc
    except InvalidInterviewInput as exc:
        raise HTTPException(422, str(exc)) from exc
    except InterviewGenerationFailed as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post(
    "/interview/sessions/{session_id}/questions/{question_id}/suggestion",
    response_model=InterviewSuggestionResponse,
)
@limiter.limit(settings.interview_suggestion_rate_limit)
def suggest_interview_answer(
    request: Request,
    session_id: str,
    question_id: str,
    store: InterviewSessionStore = Depends(interview_store_dep),
):
    """Genera o recupera de caché una respuesta sugerida para una pregunta pendiente.

    Args:
        request: Petición HTTP, requerida por el limitador de tasa.
        session_id: Identificador de la sesión de entrevista.
        question_id: Identificador de la pregunta aún no respondida.
        store: Almacén en memoria de sesiones de entrevista.

    Returns:
        InterviewSuggestionResponse: Borrador, esquema, avisos, notas y evidencia.

    Raises:
        HTTPException: 403 sin consentimiento, 404 si la sesión no existe o expiró,
            409 si la pregunta ya fue respondida, 422 si la pregunta no pertenece a
            la sesión, 429 si se supera el límite, 502 si falla el LLM y 503 si no
            está disponible la API key o el modelo no responde.
    """
    try:
        return suggest_answer(store, session_id, question_id)
    except SessionNotFound as exc:
        raise HTTPException(404, SESSION_NOT_FOUND) from exc
    except AlreadyAnswered as exc:
        raise HTTPException(409, "Ya respondiste esta pregunta.") from exc
    except InvalidInterviewInput as exc:
        raise HTTPException(422, str(exc)) from exc
    except InterviewGenerationFailed as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get(
    "/interview/sessions/{session_id}/summary",
    response_model=InterviewSummary,
)
def session_summary(
    session_id: str,
    store: InterviewSessionStore = Depends(interview_store_dep),
):
    """Resume el progreso y los resultados de una sesión de entrevista.

    Args:
        session_id: Identificador de la sesión de entrevista.
        store: Almacén en memoria de sesiones de entrevista.

    Returns:
        InterviewSummary: Puntuación global y por categoría, fortalezas, carencias
        y próximos pasos.

    Raises:
        HTTPException: 403 sin consentimiento, 404 si la sesión no existe o expiró y
            422 si aún no hay respuestas.
    """
    try:
        return build_summary(store, session_id)
    except SessionNotFound as exc:
        raise HTTPException(404, SESSION_NOT_FOUND) from exc
    except InvalidInterviewInput as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/interview/sessions/{session_id}", status_code=204)
def delete_interview(
    session_id: str,
    store: InterviewSessionStore = Depends(interview_store_dep),
):
    """Elimina una sesión de entrevista y sus datos asociados.

    Args:
        session_id: Identificador de la sesión de entrevista.
        store: Almacén en memoria de sesiones de entrevista.

    Returns:
        Response: Respuesta vacía con estado 204.

    Raises:
        HTTPException: 403 sin consentimiento y 404 si la sesión no existe o expiró.
    """
    try:
        delete_session(store, session_id)
    except SessionNotFound as exc:
        raise HTTPException(404, SESSION_NOT_FOUND) from exc
    return Response(status_code=204)
