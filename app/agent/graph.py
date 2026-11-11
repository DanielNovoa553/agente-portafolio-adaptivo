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
    """Recupera y prioriza evidencias relevantes para un análisis de oferta.

    Lanza consultas con el título, las competencias requeridas, las palabras
    clave y cada una de las cinco primeras competencias; conserva la mejor
    puntuación por ID y descarta las evidencias por debajo de `MIN_SCORE`.

    Args:
        store: Almacén vectorial del perfil profesional.
        a: Análisis estructurado de la oferta.

    Returns:
        Hasta `MAX_EVIDENCE` evidencias ordenadas de mayor a menor score.
    """
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
    """Construye y compila el grafo del agente de generación de documentos.

    El flujo analiza la oferta, recupera evidencias, genera un borrador y lo
    valida. Si la validación falla, reintenta hasta `MAX_ATTEMPTS` veces.

    Args:
        llm: Modelo de lenguaje con salida estructurada.
        store: Almacén vectorial del perfil profesional.

    Returns:
        Grafo compilado de LangGraph listo para invocar.
    """
    def analyze_job(state: AgentState) -> dict:
        """Analiza la oferta y reinicia el contador de intentos.

        Args:
            state: Estado del agente con el texto de la oferta.

        Returns:
            Actualización con el análisis estructurado y `attempts` a cero.
        """
        analysis = llm.with_structured_output(JobAnalysis).invoke(
            [("system", ANALYZE_SYSTEM), ("human", state["job_text"])]
        )
        logger.info("job analyzed", extra={"title": analysis.title})
        return {"analysis": analysis, "attempts": 0}

    def retrieve(state: AgentState) -> dict:
        """Recupera evidencias relevantes a partir del análisis de la oferta.

        Args:
            state: Estado del agente con el análisis ya calculado.

        Returns:
            Actualización con la lista de evidencias recuperadas.
        """
        relevant = retrieve_evidence(store, state["analysis"])
        logger.info("evidence retrieved", extra={"count": len(relevant)})
        return {"evidence": relevant}

    def route_after_retrieve(state: AgentState) -> str:
        """Decide si hay evidencia suficiente para generar el documento.

        Args:
            state: Estado del agente con las evidencias recuperadas.

        Returns:
            `"generate"` si hay al menos `MIN_EVIDENCE` evidencias y la mejor
            alcanza `MIN_TOP_SCORE`; en caso contrario, `"insufficient"`.
        """
        evidence = state["evidence"]
        enough = len(evidence) >= MIN_EVIDENCE and evidence[0].score >= MIN_TOP_SCORE
        return "generate" if enough else "insufficient"

    def insufficient(state: AgentState) -> dict:
        """Marca el estado como falta de evidencia y explica cómo continuar.

        Args:
            state: Estado del agente (no se modifica).

        Returns:
            Actualización con el estado `insufficient_evidence` y un mensaje.
        """
        return {
            "status": "insufficient_evidence",
            "error": "Tu perfil no tiene evidencia suficiente para esta oferta. "
            "Ingesta más información (CV, proyectos) y vuelve a intentar.",
        }

    def generate(state: AgentState) -> dict:
        """Genera un borrador estructurado para el tipo de salida solicitado.

        Incluye la oferta, los requisitos, las evidencias y, si existe, el
        feedback del intento anterior. Los errores de validación de Pydantic se
        convierten en feedback para el siguiente intento.

        Args:
            state: Estado del agente con análisis, evidencias y tipo de salida.

        Returns:
            Actualización con el borrador, el número de intentos y el feedback.
        """
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
        """Comprueba que el borrador solo cite evidencias recuperadas.

        Args:
            state: Estado del agente con el borrador y las evidencias válidas.

        Returns:
            `{"status": "ok"}` si es válido; si cita IDs inexistentes, borra el
            borrador y devuelve feedback con los IDs permitidos.
        """
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
        """Decide si el grafo termina, reintenta o falla tras validar.

        Args:
            state: Estado del agente con el resultado de la validación.

        Returns:
            `"done"` si el estado es `ok`, `"failed"` al agotar
            `MAX_ATTEMPTS`, o `"retry"` para generar otro borrador.
        """
        if state.get("status") == "ok":
            return "done"
        if state.get("attempts", 0) >= MAX_ATTEMPTS:
            return "failed"
        return "retry"

    def failed(state: AgentState) -> dict:
        """Marca el estado como fallido tras agotar los intentos.

        Args:
            state: Estado del agente con el último feedback disponible.

        Returns:
            Actualización con el estado `failed` y un mensaje de error.
        """
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
    """Ejecuta el grafo del agente para una oferta y un tipo de salida.

    Args:
        llm: Modelo de lenguaje con salida estructurada.
        store: Almacén vectorial del perfil profesional.
        job_text: Texto completo de la oferta de empleo.
        output_type: Documento que se quiere generar.

    Returns:
        Estado final del agente, incluido el borrador o el error.
    """
    graph = build_graph(llm, store)
    return graph.invoke({"job_text": job_text, "output_type": output_type})
