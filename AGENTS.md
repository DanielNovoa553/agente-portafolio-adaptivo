# Instrucciones para agentes

## Aplicación de cambios

- No aplicar cambios en archivos automáticamente (`auto_apply: false`).
- Confirmar con el usuario antes de aplicar cualquier modificación (`confirm_changes: true`).
- Antes de editar o crear archivos, mostrar el plan o el diff propuesto y esperar aprobación explícita.

## Contexto

Agente RAG que, a partir de un perfil profesional (CV, proyectos, certificaciones) y una oferta, genera documentos adaptados y validados: CV, proyectos priorizados y carta de presentación. Después permite practicar una entrevista técnica: genera preguntas a partir de la oferta y el documento, evalúa las respuestas contra la evidencia del perfil y devuelve feedback. La interfaz, los prompts y los mensajes de error están en español; los documentos, preguntas y feedback siguen el idioma de la oferta.

## Comandos

Windows; usa el venv local `.venv` y ejecuta desde la raíz del repo (los imports son `app.*` y `.env` se resuelve relativo al CWD).

```bash
.venv/Scripts/python -m pytest -q                      # 67 tests, ~5 s, sin red
.venv/Scripts/python -m pytest tests/test_agent.py::test_happy_path -q
.venv/Scripts/python -m pytest tests/test_interview.py tests/test_interview_api.py -q
.venv/Scripts/python -m uvicorn app.main:app --reload  # http://localhost:8000
.venv/Scripts/python -m scripts.list_models            # modelos Gemini de la API key
.venv/Scripts/python -m scripts.evaluate --limit 3     # smoke de la evaluación real
.venv/Scripts/python -m scripts.evaluate_interview --limit 3  # smoke de la entrevista
```

- `scripts.evaluate` completo tarda ~15 min y consume cuota de Gemini; requiere `GOOGLE_API_KEY` y `data/profile/*.md`, indexa en una base temporal (no toca `chroma_db/`) y reescribe `eval/report.{json,md}` (versionados). Sin perfil, aborta. Incluso con `--limit`, reemplaza el reporte completo: no lo ejecutes si no quieres modificarlo.
- `scripts.evaluate_interview` consume cuota de Gemini (una llamada de plan y una de evaluación por caso), usa `eval/interview_cases.json` y reescribe `eval/interview_report.{json,md}`. No toca `eval/report.{json,md}`. Con `--limit` también reemplaza el reporte de entrevista completo; no lo ejecutes si no quieres modificarlo.
- No hay lint, typecheck, formatter, pre-commit, CI ni Docker: la verificación es `pytest`.

## Arquitectura

- `app/main.py`: FastAPI, logs JSON con `X-Request-ID`, handlers de error y `StaticFiles`. El `app.mount("/", ...)` debe quedar al final; las rutas agregadas después quedan tapadas.
- `app/api/`: routers `health`, `models`, `profile` (`/ingest`, `/sources`, `DELETE /sources/{name}`), `generate` e `interview`; dependencias en `deps.py`.
- `app/agent/graph.py`: LangGraph `analyze_job → retrieve → generate → validate`, con hasta `MAX_ATTEMPTS = 3` intentos y validación de que cada `evidence_id` exista entre las evidencias recuperadas. `insufficient_evidence` → 422; fallo tras los intentos → 502. `app/agent/context.py` contiene `collect_evidence_ids` y `format_evidence`, compartidos con la entrevista (y reexportados desde `graph.py`).
- `app/interview/`: `store.py` (sesiones en memoria con TTL y máximo), `prompts.py` (prompts y delimitadores) y `service.py` (validación del plan, evaluación, score y resumen determinista). No guarda el texto de las respuestas.
- `app/schemas/interview.py`: esquemas de preguntas, evaluación, peticiones/respuestas y resumen.
- `app/rag/`: acepta `.md`, `.txt` y `.pdf`; fragmenta por encabezados Markdown, indexa en ChromaDB de forma idempotente por nombre de archivo y usa IDs `archivo::n`. `doc_type`: `cv`, `project`, `certification`, `other`.
- `app/static/`: HTML/CSS/JS sin build; se edita y sirve directamente. La pestaña Entrevista se habilita al generar un documento y todo el contenido del LLM se renderiza con `textContent`/`el()`.

## Reglas no obvias

- `/ingest`, `/sources`, `DELETE /sources/{name}`, `/generate` y todas las rutas `/interview/*` exigen `X-Privacy-Consent: 2`; `/health` y `/models` no.
- Solo se aceptan modelos devueltos por `GET /models` (o el de `LLM_MODEL`); cualquier otro → 422. La lista se cachea hasta 10 minutos. La entrevista elige el modelo al crear la sesión (`?model=`) y lo mantiene fijo hasta terminarla.
- Rate limit por IP (por defecto): `10/minute` en `/generate`, `20/minute` en `/ingest`, `5/minute` al crear una entrevista y `30/minute` al evaluar respuestas; al superarlo, 429.
- `MIN_TOP_SCORE = 0.665` y `MIN_SCORE = 0.35` en `app/agent/graph.py` están calibrados con `eval/cases.json`; cambiarlos exige volver a ejecutar `python -m scripts.evaluate`.
- Entrevista: sesiones `uuid4` en memoria, TTL 120 min y máximo 100 (se expulsa la más antigua). El servidor guarda la evaluación y la nota, nunca el texto de la respuesta; tampoco lo loguea. Un `404` indica sesión inexistente/expirada, `409` pregunta ya respondida.
- El plan debe tener exactamente `question_count` preguntas (5–10), incluir technical/project/behavioral y al menos role_fit o gap, y citar solo `evidence_ids` reales; si falla tras 2 intentos → `502`. La evaluación exige los 5 criterios exactos y evidencia válida.
- Solo están implementados Gemini y ChromaDB; `LLM_PROVIDER` no cambia de backend.
- Sin `GOOGLE_API_KEY`, las rutas que tocan el perfil o Gemini (`/ingest`, `/sources`, `/models`, `/generate`, `/interview/*`) fallan con 503; `/health` y los estáticos siguen respondiendo. La configuración se carga de `.env` con pydantic-settings.
- `chroma_db/`, `data/profile/` y `.env` son estado local ignorado por git; no los commitees.

## Tests

- Son offline: `FakeLLM` en `tests/test_agent.py` y `FakeEmbedder` en `tests/test_rag.py`; nunca uses la API real de Gemini en tests.
- Los tests de API sustituyen `store_dep`/`llm_dep` con `app.dependency_overrides`, usan `ProfileStore(path="")` (Chroma efímero) y bajan los umbrales con `monkeypatch`. Mantén ese patrón: `get_store()` está cacheado y apuntaría a `chroma_db/`.
- Los tests de entrevista sustituyen `interview_store_dep` por un `InterviewSessionStore` limpio y guionizan `GeneratedInterviewPlan`/`AnswerEvaluation` en `FakeLLM`; usan un reloj inyectable para probar el TTL y comprueban que la respuesta no se guarda ni aparece en logs.
- Los fixtures de API desactivan `limiter`; el test de rate limit lo reactiva. Restaura `limiter.enabled` y limpia `app.dependency_overrides` al terminar.
