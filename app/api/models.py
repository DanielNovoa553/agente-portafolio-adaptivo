from fastapi import APIRouter

from app.agent.models import list_models
from app.core.config import settings

router = APIRouter(tags=["models"])


@router.get("/models")
def models() -> dict:
    return {"default": settings.llm_model, "models": list_models()}
