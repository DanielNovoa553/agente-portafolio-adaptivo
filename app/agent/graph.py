import logging

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agent.context import collect_evidence_ids, format_evidence
from app.agent.prompts import ANALYZE_SYSTEM, GENERATE_SYSTEM, TASK_INSTRUCTIONS
from app.agent.state import OUTPUT_SCHEMAS, AgentState, OutputType
from app.rag.store import ProfileStore
from app.schemas.documents import JobAnalysis
from app.schemas.evidence import Evidence

__all__ = ["collect_evidence_ids", "format_evidence", "run_agent", "build_graph"]

logger = logging.getLogger("agent")

MAX_ATTEMPTS = 3
MIN_SCORE = 0.35
# Calibrado con eval/cases.json: sin encaje <= 0.64, con encaje >= 0.69 (Gemini embeddings)
MIN_TOP_SCORE = 0.665
MIN_EVIDENCE = 2
K_PER_QUERY = 4
MAX_EVIDENCE = 12


def retrieve_evidence(store: ProfileStore, a: JobAnalysis) -> list[Evidence]:
    """Evidencias relevantes (ordenadas por score) para un análisis de oferta."""
    queries = [
        a.title,
        ", ".join(a.required_skills),
        ", ".join(a.keywords),
        *a.required_skills[:5],
    ]
    found: dict[str, Evidence] = {}
    for q in queries:
        for ev in store.retrieve(q, k=K_PER_QUERY):
            if ev.id not in found or ev.score > found[ev.id].score:
                found[ev.id] = ev
    ranked = sorted(found.values(), key=lambda e: e.score, reverse=True)
    return [e for e in ranked if e.score >= MIN_SCORE][:MAX_EVIDENCE]


def build_graph(llm, store: ProfileStore):
    def analyze_job(state: AgentState) -> dict:
        analysis = llm.with_structured_output(JobAnalysis).invoke(
            [("system", ANALYZE_SYSTEM), ("human", state["job_text"])]
        )
        logger.info("job analyzed", extra={"title": analysis.title})
        return {"analysis": analysis, "attempts": 0}

    def retrieve(state: AgentState) -> dict:
        relevant = retrieve_evidence(store, state["analysis"])
        logger.info("evidence retrieved", extra={"count": len(relevant)})
        return {"evidence": relevant}

    def route_after_retrieve(state: AgentState) -> str:
        evidence = state["evidence"]
        enough = len(evidence) >= MIN_EVIDENCE and evidence[0].score >= MIN_TOP_SCORE
        return "generate" if enough else "insufficient"

    def insufficient(state: AgentState) -> dict:
        return {
            "status": "insufficient_evidence",
            "error": "Tu perfil no tiene evidencia suficiente para esta oferta. "
            "Ingesta más información (CV, proyectos) y vuelve a intentar.",
        }

    def generate(state: AgentState) -> dict:
        schema = OUTPUT_SCHEMAS[state["output_type"]]
        a = state["analysis"]
        human = (
            f"{TASK_INSTRUCTIONS[state['output_type'].value]}\n\n"
            f"OFERTA:\n{state['job_text']}\n\n"
            f"REQUISITOS: {', '.join(a.required_skills)}\n"
            f"PALABRAS CLAVE: {', '.join(a.keywords)}\n\n"
            f"EVIDENCIAS DEL PERFIL:\n{format_evidence(state['evidence'])}"
        )
        if state.get("feedback"):
            human += f"\n\nCORRIGE ESTOS ERRORES DEL INTENTO ANTERIOR:\n{state['feedback']}"
        attempts = state.get("attempts", 0) + 1
        try:
            draft = llm.with_structured_output(schema).invoke(
                [("system", GENERATE_SYSTEM), ("human", human)]
            )
            return {"draft": draft, "attempts": attempts, "feedback": ""}
        except ValidationError as exc:
            return {
                "draft": None,
                "attempts": attempts,
                "feedback": f"Salida con formato inválido: {exc.errors()[:3]}",
            }

    def validate(state: AgentState) -> dict:
        draft = state.get("draft")
        if draft is None:
            return {}
        valid_ids = {e.id for e in state["evidence"]}
        unknown = collect_evidence_ids(draft) - valid_ids
        if unknown:
            return {
                "draft": None,
                "feedback": f"Estos evidence_ids no existen: {sorted(unknown)}. "
                f"Usa solo: {sorted(valid_ids)}",
            }
        return {"status": "ok"}

    def route_after_validate(state: AgentState) -> str:
        if state.get("status") == "ok":
            return "done"
        if state.get("attempts", 0) >= MAX_ATTEMPTS:
            return "failed"
        return "retry"

    def failed(state: AgentState) -> dict:
        return {
            "status": "failed",
            "error": f"No se obtuvo una salida válida tras {MAX_ATTEMPTS} intentos. "
            f"{state.get('feedback', '')}",
        }

    g = StateGraph(AgentState)
    g.add_node("analyze_job", analyze_job)
    g.add_node("retrieve", retrieve)
    g.add_node("insufficient", insufficient)
    g.add_node("generate", generate)
    g.add_node("validate", validate)
    g.add_node("failed", failed)

    g.add_edge(START, "analyze_job")
    g.add_edge("analyze_job", "retrieve")
    g.add_conditional_edges(
        "retrieve", route_after_retrieve, {"generate": "generate", "insufficient": "insufficient"}
    )
    g.add_edge("generate", "validate")
    g.add_conditional_edges(
        "validate",
        route_after_validate,
        {"done": END, "retry": "generate", "failed": "failed"},
    )
    g.add_edge("insufficient", END)
    g.add_edge("failed", END)
    return g.compile()


def run_agent(llm, store: ProfileStore, job_text: str, output_type: OutputType) -> AgentState:
    graph = build_graph(llm, store)
    return graph.invoke({"job_text": job_text, "output_type": output_type})
