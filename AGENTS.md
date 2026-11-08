# Instrucciones para agentes

## Aplicación de cambios

- No aplicar cambios en archivos automáticamente (`auto_apply: false`).
- Confirmar con el usuario antes de aplicar cualquier modificación (`confirm_changes: true`).
- Antes de editar o crear archivos, mostrar el plan o el diff propuesto y esperar aprobación explícita.

## Contexto

Agente RAG que, a partir de un perfil profesional (CV, proyectos, certificaciones) y una oferta, genera documentos adaptados y validados: CV, proyectos priorizados y carta de presentación. La interfaz, los prompts y los mensajes de error están en español; los documentos generados siguen el idioma de la oferta.

## Comandos

Windows; usa el venv local `.venv` y ejecuta desde la raíz del repo (los imports son `app.*` y `.env` se resuelve relativo al CWD).

```bash
.venv/Scripts/python -m pytest -q                      # 29 tests, ~20 s, sin red
.venv/Scripts/python -m pytest tests/test_agent.py::test_happy_path -q
.venv/Scripts/python -m uvicorn app.main:app --reload  # http://localhost:8000
.venv/Scripts/python -m scripts.list_models            # modelos Gemini de la API key
.venv/Scripts/python -m scripts.evaluate --limit 3     # smoke de la evaluación real
```

- `scripts.evaluate` completo tarda ~15 min y consume cuota de Gemini; requiere `GOOGLE_API_KEY` y `data/profile/*.md`, indexa en una base temporal (no toca `chroma_db/`) y reescribe `eval/report.{json,md}` (versionados). Sin perfil, aborta. Incluso con `--limit`, reemplaza el reporte completo: no lo ejecutes si no quieres modificarlo.
- No hay lint, typecheck, formatter, pre-commit, CI ni Docker: la verificación es `pytest`.

## Arquitectura

- `app/main.py`: FastAPI, logs JSON con `X-Request-ID`, handlers de error y `StaticFiles`. El `app.mount("/", ...)` debe quedar al final; las rutas agregadas después quedan tapadas.
- `app/api/`: routers `health`, `models`, `profile` (`/ingest`, `/sources`, `DELETE /sources/{name}`) y `generate`; dependencias en `deps.py`.
- `app/agent/graph.py`: LangGraph `analyze_job → retrieve → generate → validate`, con hasta `MAX_ATTEMPTS = 3` intentos y validación de que cada `evidence_id` exista entre las evidencias recuperadas. `insufficient_evidence` → 422; fallo tras los intentos → 502.
- `app/rag/`: acepta `.md`, `.txt` y `.pdf`; fragmenta por encabezados Markdown, indexa en ChromaDB de forma idempotente por nombre de archivo y usa IDs `archivo::n`. `doc_type`: `cv`, `project`, `certification`, `other`.
- `app/static/`: HTML/CSS/JS sin build; se edita y sirve directamente.

## Reglas no obvias

- `/ingest`, `/sources`, `DELETE /sources/{name}` y `/generate` exigen `X-Privacy-Consent: 1`; `/health` y `/models` no.
- Solo se aceptan modelos devueltos por `GET /models` (o el de `LLM_MODEL`); cualquier otro → 422. La lista se cachea hasta 10 minutos.
- Rate limit por IP (por defecto): `10/minute` en `/generate` y `20/minute` en `/ingest`; al superarlo, 429.
- `MIN_TOP_SCORE = 0.665` y `MIN_SCORE = 0.35` en `app/agent/graph.py` están calibrados con `eval/cases.json`; cambiarlos exige volver a ejecutar `python -m scripts.evaluate`.
- Solo están implementados Gemini y ChromaDB; `LLM_PROVIDER` no cambia de backend.
- Sin `GOOGLE_API_KEY`, las rutas que tocan el perfil o Gemini (`/ingest`, `/sources`, `/models`, `/generate`) fallan con 503; `/health` y los estáticos siguen respondiendo. La configuración se carga de `.env` con pydantic-settings.
- `chroma_db/`, `data/profile/` y `.env` son estado local ignorado por git; no los commitees.

## Tests

- Son offline: `FakeLLM` en `tests/test_agent.py` y `FakeEmbedder` en `tests/test_rag.py`; nunca uses la API real de Gemini en tests.
- Los tests de API sustituyen `store_dep`/`llm_dep` con `app.dependency_overrides`, usan `ProfileStore(path="")` (Chroma efímero) y bajan los umbrales con `monkeypatch`. Mantén ese patrón: `get_store()` está cacheado y apuntaría a `chroma_db/`.
- Los fixtures de API desactivan `limiter`; el test de rate limit lo reactiva. Restaura `limiter.enabled` y limpia `app.dependency_overrides` al terminar.
