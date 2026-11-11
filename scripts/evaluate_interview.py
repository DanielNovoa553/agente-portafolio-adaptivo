"""Evaluación de calidad del preparador de entrevistas sobre eval/interview_cases.json.

Uso:
    python -m scripts.evaluate_interview [--model ID] [--limit N]

Crea una sesión por caso con el LLM real, valida el plan de preguntas, evalúa la
respuesta y escribe eval/interview_report.json y eval/interview_report.md.
No modifica eval/report.json ni eval/report.md (son del generador de documentos).
Consume cuota de Gemini y no forma parte de pytest.
"""
import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from app.agent.llm import get_llm
from app.agent.state import OutputType
from app.core.config import settings
from app.interview.service import (
    generate_plan,
    select_session_evidence,
    submit_answer,
    validate_document,
)
from app.interview.store import InterviewSessionStore
from app.schemas.evidence import Evidence
from app.schemas.interview import (
    Difficulty,
    InterviewCategory,
    InterviewPlanInternal,
    InterviewQuestionInternal,
    InterviewStartRequest,
)

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "interview_cases.json"


def pct(num: int, den: int) -> str:
    """Formatea una proporción como "num/den (porcentaje)".

    Args:
        num: Numerador de la proporción.
        den: Denominador de la proporción.

    Returns:
        str: Texto con la proporción y el porcentaje, o "n/a" si el denominador es 0.
    """
    return f"{num}/{den} ({num / den:.0%})" if den else "n/a"


def load_case(case: dict):
    """Valida un caso y prepara el documento y la evidencia de la sesión.

    Args:
        case: Caso de evaluación con oferta, documento, evidencias y tipo de salida.

    Returns:
        tuple: Tipo de salida, evidencias originales, documento validado y evidencias
        seleccionadas para la sesión.
    """
    output_type = OutputType(case["output_type"])
    evidence = [Evidence(**item) for item in case["evidence"]]
    request = InterviewStartRequest(
        job_text=case["job_text"],
        output_type=output_type,
        document=case["document"],
        evidence=evidence,
        question_count=6,
    )
    parsed, document = validate_document(request)
    selected = select_session_evidence(parsed, evidence, settings.interview_max_evidence)
    return output_type, evidence, document, selected


def check_plan(llm, case: dict, output_type, document, evidence) -> tuple[bool, str | None]:
    """Comprueba que el LLM genera un plan de entrevista válido para el caso.

    Args:
        llm: Cliente LLM usado para generar el plan.
        case: Caso de evaluación con la oferta.
        output_type: Tipo de documento de salida.
        document: Documento validado del caso.
        evidence: Evidencias seleccionadas para la sesión.

    Returns:
        tuple[bool, str | None]: Éxito de la generación y mensaje de error si falla.
    """
    try:
        generate_plan(
            llm,
            job_text=case["job_text"],
            output_type=output_type,
            document=document,
            evidence=evidence,
            question_count=6,
        )
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def evaluate_case(llm, case: dict, output_type, document, evidence, model: str):
    """Evalúa la respuesta del caso en una sesión de entrevista temporal.

    Args:
        llm: Cliente LLM usado para evaluar la respuesta.
        case: Caso con la pregunta y la respuesta a evaluar.
        output_type: Tipo de documento de salida.
        document: Documento validado del caso.
        evidence: Evidencias seleccionadas para la sesión.
        model: Identificador del modelo evaluado.

    Returns:
        InterviewAnswerResponse: Evaluación de la respuesta del caso.
    """
    question_data = case["question"]
    question = InterviewQuestionInternal(
        id="q1",
        category=InterviewCategory(question_data["category"]),
        difficulty=Difficulty(question_data["difficulty"]),
        question=question_data["question"],
        why_asked=question_data["why_asked"],
        evidence_ids=question_data.get("evidence_ids", []),
        expected_points=question_data["expected_points"],
    )
    plan = InterviewPlanInternal(
        role_title=case["id"],
        focus_areas=["Evaluación"],
        questions=[question],
    )
    store = InterviewSessionStore(ttl_minutes=120, max_sessions=10)
    session = store.create(
        job_text=case["job_text"],
        output_type=output_type,
        document=document,
        evidence=evidence,
        plan=plan,
        llm=llm,
        model_id=model,
    )
    try:
        return submit_answer(store, session.id, "q1", case["answer"])
    finally:
        store.reset()


def run_case(llm, case: dict, model: str) -> dict:
    """Ejecuta las comprobaciones de un caso de entrevista.

    Args:
        llm: Cliente LLM usado en la evaluación.
        case: Caso de evaluación con pregunta, respuesta y expectativas.
        model: Identificador del modelo evaluado.

    Returns:
        dict: Fila con validez del plan, score, veredicto, checks y errores.
    """
    started = time.time()
    row = {
        "id": case["id"],
        "kind": case.get("kind", "question"),
        "category": case["question"]["category"],
        "pair": case.get("pair"),
        "quality": case.get("quality"),
    }
    try:
        output_type, evidence, document, selected = load_case(case)
    except Exception as exc:
        row.update(
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
            plan_valid=False,
            passed=False,
        )
        return row

    plan_valid, plan_error = check_plan(llm, case, output_type, document, selected)
    row["plan_valid"] = plan_valid
    if plan_error:
        row["plan_error"] = plan_error

    try:
        result = evaluate_case(llm, case, output_type, document, selected, model)
    except Exception as exc:
        row.update(
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
            passed=False,
            seconds=round(time.time() - started, 1),
        )
        return row

    expect = case.get("expect", {})
    checks = {}
    if "min_score" in expect:
        checks["min_score"] = result.score >= expect["min_score"]
    if "max_score" in expect:
        checks["max_score"] = result.score <= expect["max_score"]
    if "must_cite" in expect:
        cited = {e.id for e in result.evidence}
        checks["must_cite"] = all(item in cited for item in expect["must_cite"])
    if "must_flag_unsupported" in expect:
        checks["must_flag_unsupported"] = (
            bool(result.unsupported_claims) == expect["must_flag_unsupported"]
        )

    row.update(
        score=result.score,
        verdict=result.verdict.value,
        criteria={c.criterion.value: c.score for c in result.criteria},
        cited=[e.id for e in result.evidence],
        unsupported_claims=result.unsupported_claims,
        checks=checks,
        passed=all(checks.values()),
        seconds=round(time.time() - started, 1),
    )
    return row


def summarize(rows: list[dict]) -> dict:
    """Agrega las métricas de los casos de entrevista ejecutados.

    Args:
        rows: Filas devueltas por `run_case`.

    Returns:
        dict: Totales de validez, expectativas, evidencia y separación fuerte/débil.
    """
    evaluated = [r for r in rows if "error" not in r]
    plan_ok = sum(1 for r in rows if r.get("plan_valid"))
    pair_scores: dict[str, dict[str, int]] = {}
    for r in evaluated:
        if r.get("pair") and r.get("quality") in {"strong", "weak"}:
            pair_scores.setdefault(r["pair"], {})[r["quality"]] = r["score"]
    separation = {
        pair: scores["strong"] - scores["weak"]
        for pair, scores in pair_scores.items()
        if "strong" in scores and "weak" in scores
    }
    cite_checks = [
        r["checks"]["must_cite"] for r in evaluated if "must_cite" in r.get("checks", {})
    ]
    return {
        "cases": len(rows),
        "errors": len(rows) - len(evaluated),
        "plan_valid": (plan_ok, len(rows)),
        "evaluation_valid": (len(evaluated), len(rows)),
        "expectations": (sum(1 for r in evaluated if r.get("passed")), len(evaluated)),
        "feedback_with_evidence": (sum(cite_checks), len(cite_checks)),
        "separation": separation,
        "separation_pairs_ok": (
            sum(1 for delta in separation.values() if delta >= 20),
            len(separation),
        ),
        "injection_cases": [r["id"] for r in rows if r.get("kind") == "injection"],
    }


def write_reports(rows, summary, model, out_dir: Path) -> None:
    """Escribe el reporte de entrevista en formato JSON y Markdown.

    Args:
        rows: Filas con el detalle de cada caso.
        summary: Métricas agregadas de la evaluación.
        model: Identificador del modelo evaluado.
        out_dir: Directorio donde se escriben `interview_report.json` y
            `interview_report.md`.
    """
    (out_dir / "interview_report.json").write_text(
        json.dumps(
            {"model": model, "summary": summary, "cases": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    s = summary
    separation = ", ".join(
        f"{pair}: {delta} pts" for pair, delta in s["separation"].items()
    ) or "n/a"
    lines = [
        "# Reporte de evaluación del preparador de entrevistas",
        "",
        f"Fecha: {date.today()} · Modelo: `{model}` · Casos: {s['cases']} "
        f"({s['errors']} con error)",
        "",
        "| Métrica | Resultado |",
        "|---|---|",
        f"| Plan válido (esquema, número, categorías, evidencia) | {pct(*s['plan_valid'])} |",
        f"| Evaluación válida (5 criterios, evidencia válida) | {pct(*s['evaluation_valid'])} |",
        f"| Expectativas automáticas cumplidas | {pct(*s['expectations'])} |",
        f"| Feedback con evidencia requerida | {pct(*s['feedback_with_evidence'])} |",
        f"| Separación fuerte vs débil (objetivo ≥ 20) | {separation} "
        f"({pct(*s['separation_pairs_ok'])}) |",
        f"| Casos de inyección para revisión manual | "
        f"{', '.join(s['injection_cases']) or 'n/a'} |",
        "",
        "> La evaluación con LLM no es determinista. Revisa a mano los casos de inyección "
        "y las afirmaciones no respaldadas. No uses este reporte para decisiones de "
        "contratación.",
        "",
        "## Detalle",
        "",
        "| Caso | Tipo | Plan | Score | Veredicto | Checks | Evidencias | No respaldadas |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        plan = "OK" if r.get("plan_valid") else "FALLA"
        if "error" in r:
            lines.append(
                f"| {r['id']} | {r['kind']} | {plan} | ERROR: {r['error']} | | | | |"
            )
            continue
        checks = ", ".join(
            f"{name}:{'OK' if ok else 'FALLA'}" for name, ok in r["checks"].items()
        ) or "sin checks"
        unsupported = "; ".join(r["unsupported_claims"]) or "–"
        cited = ", ".join(r["cited"]) or "–"
        lines.append(
            f"| {r['id']} | {r['kind']} | {plan} | {r['score']} | {r['verdict']} | "
            f"{checks} | {cited} | {unsupported} |"
        )
    (out_dir / "interview_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Ejecuta la evaluación y reescribe eval/interview_report.{json,md}.

    Evalúa como máximo --limit casos de eval/interview_cases.json con el modelo
    --model y escribe el reporte en eval/ sin tocar los reportes del generador.
    Termina si falta la API key, no existe el archivo de casos o no hay casos.

    Raises:
        SystemExit: Si falta GOOGLE_API_KEY, no existe el archivo de casos o la
            lista de casos está vacía.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not settings.google_api_key:
        sys.exit("Falta GOOGLE_API_KEY en .env")
    if not CASES.exists():
        sys.exit(f"No existe {CASES}")

    cases = json.loads(CASES.read_text(encoding="utf-8"))[: args.limit]
    if not cases:
        sys.exit("No hay casos para evaluar")
    model = args.model or settings.llm_model
    llm = get_llm(model)

    rows = []
    for i, case in enumerate(cases, 1):
        row = run_case(llm, case, model)
        rows.append(row)
        print(f"[{i}/{len(cases)}] {row['id']}: {row.get('verdict', row.get('error'))}")

    summary = summarize(rows)
    write_reports(rows, summary, model, ROOT / "eval")
    print("\n" + (ROOT / "eval" / "interview_report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
