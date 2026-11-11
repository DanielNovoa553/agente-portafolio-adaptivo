"""Evaluación del agente sobre eval/cases.json.

Uso:
    python -m scripts.evaluate [--model ID] [--limit N] [--profile-dir data/profile]

Indexa tu perfil en una base temporal (no toca chroma_db), ejecuta el agente
con cada oferta y escribe eval/report.json y eval/report.md.
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

from app.agent.graph import MIN_EVIDENCE, MIN_SCORE, MIN_TOP_SCORE, run_agent
from app.agent.llm import get_llm
from app.agent.state import OutputType
from app.core.config import settings
from app.rag.ingest import ingest_file
from app.rag.store import ProfileStore
from app.schemas.evidence import DocType

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "eval" / "cases.json"
TOP_K = 5
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

DOC_TYPES = {"cv": DocType.cv, "projects": DocType.project, "certifications": DocType.certification}


def pct(num: int, den: int) -> str:
    """Formatea una proporción como "num/den (porcentaje)".

    Args:
        num: Numerador de la proporción.
        den: Denominador de la proporción.

    Returns:
        str: Texto con la proporción y el porcentaje, o "n/a" si el denominador es 0.
    """
    return f"{num}/{den} ({num / den:.0%})" if den else "n/a"


def load_profile(profile_dir: Path) -> ProfileStore:
    """Indexa los archivos Markdown del perfil en un almacén efímero.

    Args:
        profile_dir: Directorio que contiene los archivos .md del perfil.

    Returns:
        ProfileStore: Almacén en memoria con los fragmentos indexados.
    """
    store = ProfileStore(path="")
    for f in sorted(profile_dir.glob("*.md")):
        n = ingest_file(store, f.name, f.read_bytes(), DOC_TYPES.get(f.stem, DocType.other))
        print(f"  indexado {f.name}: {n} fragmentos")
    return store


def texts_of(obj) -> list[str]:
    """Recorre un documento y devuelve todas sus cadenas.

    Excluye los valores de las claves "evidence_ids".

    Args:
        obj: Valor arbitrario del documento (str, dict, list u otro).

    Returns:
        list[str]: Cadenas encontradas en el documento.
    """
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [t for k, v in obj.items() if k != "evidence_ids" for t in texts_of(v)]
    if isinstance(obj, list):
        return [t for v in obj for t in texts_of(v)]
    return []


def unsupported_numbers(document: dict, evidence, job_text: str) -> list[str]:
    """Detecta cifras del documento ausentes en la evidencia y en la oferta.

    Args:
        document: Documento generado, serializado como diccionario.
        evidence: Evidencias recuperadas que respaldan el documento.
        job_text: Texto de la oferta.

    Returns:
        list[str]: Cifras del documento sin respaldo, ordenadas como texto.
    """
    source = " ".join([e.text for e in evidence] + [job_text])
    allowed = set(NUMBER.findall(source))
    found = set(NUMBER.findall(" ".join(texts_of(document))))
    return sorted(found - allowed)


def recall_at_k(expected: list[str], evidence) -> tuple[list[str], list[str]]:
    """Calcula qué fragmentos esperados aparecen en el top de evidencias.

    Args:
        expected: Fragmentos de texto esperados en la evidencia recuperada.
        evidence: Evidencias recuperadas, ordenadas por relevancia.

    Returns:
        tuple[list[str], list[str]]: Textos encontrados y textos no encontrados.
    """
    haystack = " ".join(f"{e.section} {e.text}" for e in evidence[:TOP_K]).lower()
    hit = [x for x in expected if x.lower() in haystack]
    return hit, [x for x in expected if x not in hit]


def run_case(llm, store, case: dict) -> dict:
    """Ejecuta el agente sobre un caso y recopila sus métricas.

    Reintenta una vez si el proveedor lanza una excepción.

    Args:
        llm: Cliente LLM usado por el agente.
        store: Almacén del perfil con la evidencia indexada.
        case: Caso de evaluación con oferta, tipo de salida y expectativas.

    Returns:
        dict: Fila con estado, intentos, evidencias, métricas y posibles errores.
    """
    start = time.time()
    state = None
    for attempt in range(2):
        try:
            state = run_agent(llm, store, case["job_text"], OutputType(case["output_type"]))
            break
        except Exception as exc:  # error del proveedor: un reintento
            err = f"{type(exc).__name__}: {str(exc)[:120]}"
            if attempt == 0:
                time.sleep(8)
    if state is None:
        return {"id": case["id"], "error": err, "should_match": case["should_match"]}

    evidence = state.get("evidence", [])
    status = state.get("status", "failed")
    row = {
        "id": case["id"],
        "should_match": case["should_match"],
        "output_type": case["output_type"],
        "status": status,
        "attempts": state.get("attempts", 0),
        "evidence": len(evidence),
        "top_score": evidence[0].score if evidence else 0.0,
        "seconds": round(time.time() - start, 1),
    }
    if case["should_match"]:
        hit, missed = recall_at_k(case["expected"], evidence)
        row.update(hits=len(hit), expected=len(case["expected"]), missed=missed)
    if status == "ok":
        doc = state["draft"].model_dump(mode="json")
        row["unsupported_numbers"] = unsupported_numbers(doc, evidence, case["job_text"])
    return row


def summarize(rows: list[dict]) -> dict:
    """Agrega las métricas de los casos ejecutados.

    Args:
        rows: Filas devueltas por `run_case`.

    Returns:
        dict: Totales y proporciones de recall, generación, fidelidad y rechazos.
    """
    ok_rows = [r for r in rows if "error" not in r]
    pos = [r for r in ok_rows if r["should_match"]]
    neg = [r for r in ok_rows if not r["should_match"]]
    generated = [r for r in pos if r["status"] == "ok"]
    return {
        "cases": len(rows),
        "errors": len(rows) - len(ok_rows),
        "recall_at_5": (sum(r["hits"] for r in pos), sum(r["expected"] for r in pos)),
        "valid_first_try": (sum(r["attempts"] == 1 for r in generated), len(generated)),
        "generated_ok": (len(generated), len(pos)),
        "faithful": (sum(not r["unsupported_numbers"] for r in generated), len(generated)),
        "correct_rejections": (sum(r["status"] == "insufficient_evidence" for r in neg), len(neg)),
        "false_rejections": (sum(r["status"] == "insufficient_evidence" for r in pos), len(pos)),
    }


def write_reports(rows, summary, model, out_dir: Path) -> None:
    """Escribe el reporte de evaluación en formato JSON y Markdown.

    Args:
        rows: Filas con el detalle de cada caso.
        summary: Métricas agregadas de la evaluación.
        model: Identificador del modelo evaluado.
        out_dir: Directorio donde se escriben `report.json` y `report.md`.
    """
    (out_dir / "report.json").write_text(
        json.dumps({"model": model, "summary": summary, "cases": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    s = summary
    lines = [
        "# Reporte de evaluación",
        "",
        f"Fecha: {date.today()} · Modelo: `{model}` · Embeddings: `{settings.embedding_model}` · "
        f"MIN_SCORE: {MIN_SCORE} · MIN_TOP_SCORE: {MIN_TOP_SCORE} · Casos: {s['cases']} ({s['errors']} con error)",
        "",
        "| Métrica | Resultado |",
        "|---|---|",
        f"| Recall@{TOP_K} (evidencia esperada en el top {TOP_K}) | {pct(*s['recall_at_5'])} |",
        f"| Documento generado con éxito (ofertas con encaje) | {pct(*s['generated_ok'])} |",
        f"| Validación Pydantic al primer intento | {pct(*s['valid_first_try'])} |",
        f"| Fidelidad (sin cifras inventadas) | {pct(*s['faithful'])} |",
        f"| Rechazos correctos (ofertas sin encaje) | {pct(*s['correct_rejections'])} |",
        f"| Falsos rechazos (ofertas con encaje) | {pct(*s['false_rejections'])} |",
        "",
        "## Detalle",
        "",
        "| Caso | Estado | Intentos | Evidencias | Mejor score | Recall | Cifras sin respaldo |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['id']} | ERROR: {r['error']} | | | | | |")
            continue
        recall = f"{r['hits']}/{r['expected']}" if "hits" in r else "–"
        nums = ", ".join(r.get("unsupported_numbers", [])) or "–"
        lines.append(
            f"| {r['id']} | {r['status']} | {r['attempts']} | {r['evidence']} | "
            f"{r['top_score']:.2f} | {recall} | {nums} |"
        )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Ejecuta la evaluación y reescribe eval/report.json y eval/report.md.

    Indexa el perfil indicado por --profile-dir en una base temporal, evalúa como
    máximo --limit casos de eval/cases.json con el modelo --model y escribe el
    reporte en eval/. Termina si falta la API key o no hay archivos de perfil.

    Raises:
        SystemExit: Si falta GOOGLE_API_KEY o no hay archivos .md en el perfil.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--profile-dir", default=str(ROOT / "data" / "profile"))
    args = parser.parse_args()

    if not settings.google_api_key:
        sys.exit("Falta GOOGLE_API_KEY en .env")
    profile_dir = Path(args.profile_dir)
    if not list(profile_dir.glob("*.md")):
        sys.exit(f"No hay archivos .md en {profile_dir}")

    cases = json.loads(CASES.read_text(encoding="utf-8"))[: args.limit]
    model = args.model or settings.llm_model
    print(f"Indexando perfil de {profile_dir}…")
    store = load_profile(profile_dir)
    llm = get_llm(model)

    rows = []
    for i, case in enumerate(cases, 1):
        row = run_case(llm, store, case)
        rows.append(row)
        print(f"[{i}/{len(cases)}] {row['id']}: {row.get('status', row.get('error'))}")

    summary = summarize(rows)
    write_reports(rows, summary, model, ROOT / "eval")
    print("\n" + (ROOT / "eval" / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
