from fastapi import Header, HTTPException, Query

from app.agent.llm import get_llm
from app.agent.models import is_allowed
from app.interview.store import InterviewSessionStore, get_interview_store
from app.rag.store import ProfileStore, get_store

PRIVACY_VERSION = "2"


def store_dep() -> ProfileStore:
    """Obtiene el almacén del perfil usado por los endpoints.

    Returns:
        ProfileStore: Instancia cacheada del almacén de ChromaDB.
    """
    return get_store()


def interview_store_dep() -> InterviewSessionStore:
    """Obtiene el almacén en memoria de sesiones de entrevista.

    Returns:
        InterviewSessionStore: Almacén compartido de sesiones de entrevista.
    """
    return get_interview_store()


def llm_dep(model: str | None = Query(None, max_length=100)):
    """Valida el modelo solicitado y construye el cliente LLM.

    Args:
        model: Identificador del modelo recibido en la query; si es None se usa el
            modelo por defecto de la configuración.

    Returns:
        ChatGoogleGenerativeAI: Cliente LLM de LangChain configurado para el modelo
        solicitado.

    Raises:
        HTTPException: 422 si el modelo no está disponible o la query no es válida.
        RuntimeError: Si falta GOOGLE_API_KEY; el manejador global lo traduce a 503.
    """
    if model and not is_allowed(model):
        raise HTTPException(422, f"Modelo no disponible: {model}")
    return get_llm(model)


def consent_dep(x_privacy_consent: str | None = Header(None)) -> None:
    """Exige la versión vigente del consentimiento de privacidad.

    Args:
        x_privacy_consent: Cabecera X-Privacy-Consent enviada por el cliente.

    Raises:
        HTTPException: 403 si la cabecera no coincide con PRIVACY_VERSION.
    """
    if x_privacy_consent != PRIVACY_VERSION:
        raise HTTPException(403, "Debes aceptar el aviso de privacidad para usar la aplicación.")
