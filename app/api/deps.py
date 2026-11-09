from fastapi import Header, HTTPException, Query

from app.agent.llm import get_llm
from app.agent.models import is_allowed
from app.interview.store import InterviewSessionStore, get_interview_store
from app.rag.store import ProfileStore, get_store

PRIVACY_VERSION = "2"


def store_dep() -> ProfileStore:
    return get_store()


def interview_store_dep() -> InterviewSessionStore:
    return get_interview_store()


def llm_dep(model: str | None = Query(None, max_length=100)):
    if model and not is_allowed(model):
        raise HTTPException(422, f"Modelo no disponible: {model}")
    return get_llm(model)


def consent_dep(x_privacy_consent: str | None = Header(None)) -> None:
    if x_privacy_consent != PRIVACY_VERSION:
        raise HTTPException(403, "Debes aceptar el aviso de privacidad para usar la aplicación.")
