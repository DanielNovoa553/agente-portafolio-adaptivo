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
    try:
        delete_session(store, session_id)
    except SessionNotFound as exc:
        raise HTTPException(404, SESSION_NOT_FOUND) from exc
    return Response(status_code=204)
