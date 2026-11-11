import logging
import time

from google import genai

from app.core.config import settings

logger = logging.getLogger("app")

MAX_MODELS = 10
CACHE_TTL = 600
EXCLUDED = ("tts", "image", "transcribe", "robotics", "computer-use", "customtools", "omni", "live", "audio", "embedding")

_cache: dict = {"at": 0.0, "models": []}


def _rank(model_id: str) -> tuple:
    """Calcula la clave de orden para priorizar los modelos disponibles.

    Args:
        model_id: Identificador del modelo sin el prefijo `models/`.

    Returns:
        Tupla que ordena primero el modelo por defecto, después los no preview,
        luego lite/flash/otros y finalmente por identificador.
    """
    # El modelo por defecto primero, luego los más ligeros/estables antes que los preview
    return (
        model_id != settings.llm_model,
        "preview" in model_id,
        0 if "lite" in model_id else 1 if "flash" in model_id else 2,
        model_id,
    )


def fetch_models() -> list[dict[str, str]]:
    """Consulta la API de Gemini y devuelve los modelos utilizables.

    Solo incluye modelos `gemini` con acción `generateContent` y excluye los
    orientados a TTS, imágenes, audio, embeddings u otras tareas.

    Returns:
        Hasta `MAX_MODELS` diccionarios con `id` y `name`, ya ordenados.
    """
    client = genai.Client(api_key=settings.google_api_key)
    found = []
    for m in client.models.list():
        actions = m.supported_actions or []
        model_id = (m.name or "").removeprefix("models/")
        if "generateContent" not in actions or not model_id.startswith("gemini"):
            continue
        if any(word in model_id for word in EXCLUDED):
            continue
        found.append({"id": model_id, "name": m.display_name or model_id})
    found.sort(key=lambda m: _rank(m["id"]))
    return found[:MAX_MODELS]


def list_models(force: bool = False) -> list[dict[str, str]]:
    """Devuelve los modelos disponibles con caché de `CACHE_TTL` segundos.

    Si la consulta falla, reutiliza la última caché o, si está vacía, devuelve
    el modelo configurado en `settings.llm_model`.

    Args:
        force: Si es `True`, ignora la caché y vuelve a consultar la API.

    Returns:
        Lista de diccionarios con `id` y `name` de cada modelo.

    Raises:
        RuntimeError: Si falta `GOOGLE_API_KEY` en la configuración.
    """
    if not settings.google_api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env")
    if not force and _cache["models"] and time.time() - _cache["at"] < CACHE_TTL:
        return _cache["models"]
    try:
        models = fetch_models()
    except Exception:
        logger.exception("no se pudo listar los modelos de Gemini")
        if _cache["models"]:
            return _cache["models"]
        return [{"id": settings.llm_model, "name": settings.llm_model}]
    _cache.update(at=time.time(), models=models)
    return models


def is_allowed(model_id: str) -> bool:
    """Indica si un modelo está permitido para las peticiones.

    Args:
        model_id: Identificador del modelo solicitado.

    Returns:
        `True` si es el modelo configurado o aparece en `list_models()`.
    """
    return model_id == settings.llm_model or any(m["id"] == model_id for m in list_models())
