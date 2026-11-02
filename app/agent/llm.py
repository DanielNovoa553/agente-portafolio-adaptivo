from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings


def get_llm(model: str | None = None):
    if not settings.google_api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env")
    return ChatGoogleGenerativeAI(
        model=model or settings.llm_model,
        google_api_key=settings.google_api_key,
        temperature=0.2,
        timeout=90,
        max_retries=2,
    )
