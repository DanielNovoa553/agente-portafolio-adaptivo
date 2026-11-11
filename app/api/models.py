from fastapi import APIRouter

from app.agent.models import list_models
from app.core.config import settings

router = APIRouter(tags=["models"])


@router.get("/models")
def models() -> dict:
    """Lista los modelos disponibles y el modelo por defecto.

    Returns:
        dict: Diccionario con las claves "default" y "models".

    Raises:
        RuntimeError: Si falta GOOGLE_API_KEY; el manejador global lo traduce a 503.
    """
    return {"default": settings.llm_model, "models": list_models()}
