from fastapi import APIRouter, Depends, HTTPException, Request

from app.agent.graph import collect_evidence_ids, run_agent
from app.api.deps import consent_dep, llm_dep, store_dep
from app.core.config import settings
from app.core.limiter import limiter
from app.rag.store import ProfileStore
from app.schemas.api import GenerateRequest, GenerateResponse

router = APIRouter(tags=["generate"], dependencies=[Depends(consent_dep)])


@router.post("/generate", response_model=GenerateResponse)
@limiter.limit(settings.generate_rate_limit)
def generate(
    request: Request,
    body: GenerateRequest,
    store: ProfileStore = Depends(store_dep),
    llm=Depends(llm_dep),
):
    state = run_agent(llm, store, body.job_text, body.output_type)
    status = state.get("status")
    if status == "insufficient_evidence":
        raise HTTPException(422, state["error"])
    if status != "ok":
        raise HTTPException(502, state.get("error", "No se pudo generar el documento"))

    used_ids = collect_evidence_ids(state["draft"])
    return GenerateResponse(
        output_type=body.output_type,
        document=state["draft"].model_dump(mode="json"),
        evidence=[e for e in state["evidence"] if e.id in used_ids],
        attempts=state["attempts"],
    )
