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
    # El modelo por defecto primero, luego los más ligeros/estables antes que los preview
    return (
        model_id != settings.llm_model,
        "preview" in model_id,
        0 if "lite" in model_id else 1 if "flash" in model_id else 2,
        model_id,
    )


def fetch_models() -> list[dict[str, str]]:
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
    return model_id == settings.llm_model or any(m["id"] == model_id for m in list_models())
