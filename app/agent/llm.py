from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings


def get_llm(model: str | None = None):
    """Crea el cliente de Gemini configurado para el agente.

    Usa temperatura 0.2, timeout de 90 segundos y hasta 2 reintentos.

    Args:
        model: Identificador del modelo; si se omite, usa `settings.llm_model`.

    Returns:
        Instancia de `ChatGoogleGenerativeAI` lista para invocar.

    Raises:
        RuntimeError: Si falta `GOOGLE_API_KEY` en la configuración.
    """
    if not settings.google_api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env")
    return ChatGoogleGenerativeAI(
        model=model or settings.llm_model,
        google_api_key=settings.google_api_key,
        temperature=0.2,
        timeout=90,
        max_retries=2,
    )
