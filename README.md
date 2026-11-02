# 🎯 Agente de Portafolio Adaptativo y Generador de Documentos

Agente de IA que, a partir de tu perfil profesional (CV, proyectos, certificaciones) y una oferta de trabajo, **genera documentos personalizados y validados**: CV adaptado, resumen de proyectos priorizados y borrador de carta de presentación.

<p align="center">
  <img src="docs/demo.gif" alt="Demo: pegar una oferta, generar un CV adaptado y ver las evidencias" width="900">
</p>

> Estado: ✅ versión local completa (fases 1–4b) · ⏸️ despliegue público fuera del alcance actual

## ¿Qué problema resuelve?

Personalizar un CV y una carta para cada oferta es lento y repetitivo. Este sistema automatiza el proceso usando **solo evidencia real de tu perfil** (RAG), evitando alucinaciones y formatos inconsistentes mediante validación estricta con Pydantic.

## Funcionalidades

1. **Ingesta de perfil**: carga de CV, proyectos y certificaciones (Markdown, TXT o PDF) desde la interfaz; se fragmentan por encabezados, se vectorizan con Gemini y se guardan en ChromaDB (idempotente por archivo).
2. **Análisis de oferta**: el usuario pega la descripción del puesto; el LLM extrae habilidades, requisitos y palabras clave.
3. **Orquestación con LangGraph**: `analyze_job → retrieve → generate → validate`, con hasta 3 reintentos si la salida es inválida o cita evidencias inexistentes.
4. **Salidas validadas** (esquemas Pydantic), cada una con `evidence_ids` que apuntan a fragmentos reales de tu perfil:
   - CV adaptado a la oferta
   - Resumen de proyectos priorizados por relevancia
   - Borrador de carta de presentación
5. **Anti-alucinación**: si el perfil no respalda la oferta (similitud baja), el agente responde `insufficient_evidence` en lugar de inventar.
6. **Selector de modelo**: la interfaz lista hasta 10 modelos Gemini disponibles para tu API key (`GET /models`).
7. **Aviso de privacidad obligatorio**: sin aceptarlo, la interfaz queda bloqueada y la API responde 403.
8. **Producción**: rate limiting (slowapi), logs JSON con `X-Request-ID`, errores claros (413/415/422/429/502/503) y mensaje amistoso cuando Gemini está saturado.

## Alcance del proyecto

La versión entregada es una aplicación funcional para ejecutarse **localmente**. Su alcance incluye cargar un perfil profesional, analizar una oferta y generar documentos personalizados respaldados por evidencias del perfil, además de la API, la interfaz web y las pruebas descritas en este README.

No incluye un servicio público multiusuario ni el despliegue en un proveedor cloud. Antes de publicar una demo habría que definir cómo aislar los perfiles de distintos usuarios y controlar el consumo de la API de Gemini. Por eso, los elementos de despliegue se consideran posibles mejoras futuras, no trabajo pendiente para usar la versión local.

## Interfaz web

Una sola página (Bootstrap 5 + JS vanilla, sin build) servida por FastAPI desde `app/static/`:

- Aviso de privacidad al entrar (hay que aceptarlo para usar la app).
- Subir/ingestar el perfil y gestionar las fuentes indexadas.
- Pegar la oferta, elegir el modelo de IA y el tipo de documento (CV, proyectos, carta).
- Ver el resultado junto con las evidencias del perfil usadas.
- Copiar o descargar el documento en Markdown.
- Modo claro/oscuro, estados de carga y mensajes de error claros.

## Arquitectura

```mermaid
flowchart LR
    U[Usuario] -->|oferta de trabajo| API[FastAPI]
    P[CV / Proyectos / Certificaciones] -->|ingesta| VDB[(ChromaDB)]
    API --> G[Grafo LangGraph]
    subgraph G[Agente LangGraph]
      A[Extraer keywords de la oferta] --> B[Recuperar evidencia - RAG]
      B --> C[Generar documento]
      C --> D{Validación Pydantic}
      D -- inválido --> C
      D -- válido --> E[Salida final]
    end
    B <--> VDB
    E --> API --> U
```

## Stack

| Capa | Tecnología |
|---|---|
| API | FastAPI |
| Frontend | JavaScript vanilla + Bootstrap 5 (servido como estáticos por FastAPI) |
| Agente | LangGraph |
| Vector DB | ChromaDB (o Qdrant) |
| Validación | Pydantic v2 |
| LLM | Google Gemini (capa gratuita, vía Google AI Studio) con `langchain-google-genai` |
| Embeddings | Gemini `gemini-embedding-001` (misma API key, capa gratuita) |
| Despliegue | Docker + Railway/Render/HF Spaces |
| Calidad | pytest, slowapi, logs estructurados |

## Estructura

```
agente-portafolio-adaptivo/
├── app/
│   ├── main.py            # FastAPI, middleware, errores, estáticos
│   ├── api/               # endpoints (health, models, ingest/sources, generate)
│   ├── agent/             # grafo LangGraph, prompts, LLM y listado de modelos
│   ├── rag/               # carga, chunking, embeddings y ChromaDB
│   ├── schemas/           # modelos Pydantic
│   ├── static/            # frontend (index.html, app.js, styles.css)
│   └── core/              # config, logging, rate limit
├── scripts/               # list_models.py, evaluate.py
├── eval/                  # cases.json (20 ofertas) y reportes
├── data/profile/          # tu perfil en Markdown (ignorado por git)
├── chroma_db/             # índice vectorial local (ignorado por git)
└── tests/                 # 29 tests (sin red: LLM y embeddings falsos)
```

## Decisiones de diseño

- **¿Por qué LangGraph y no una cadena simple?** Flujo con estado, ramas condicionales (fallback sin evidencia) y reintentos por validación fallida.
- **¿Por qué Pydantic y no solo prompt engineering?** Garantiza salidas válidas y tipadas; permite reintentos automáticos con feedback del error.
- **¿Por qué RAG?** El documento solo puede afirmar lo que está respaldado por tu perfil, y cada afirmación cita sus `evidence_ids`.
- **¿Por qué un umbral de similitud?** Evita generar documentos para ofertas que no encajan. Se calibró con el set de evaluación (ver resultados abajo).

## Instalación y uso

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # y pega tu GOOGLE_API_KEY
uvicorn app.main:app --reload
```

Abre `http://localhost:8000`, acepta el aviso de privacidad y sube tus archivos de `data/profile/`.

Otros comandos:

```bash
pytest                                   # tests
python -m scripts.list_models            # modelos Gemini disponibles
python -m scripts.evaluate               # evaluación (~15 min, usa tu cuota de Gemini)
```

La API key se obtiene gratis en [Google AI Studio](https://aistudio.google.com/apikey). Nunca la subas al repositorio (`.env` está en `.gitignore`).

## Estado del desarrollo

Las fases previstas para la versión local están terminadas:

### Fase 1: cimientos del proyecto
- [x] Crear repositorio Git y `.gitignore` (excluir `.venv`, `.env`, `chroma_db/`)
- [x] Crear la estructura de carpetas propuesta
- [x] Crear `requirements.txt` / `pyproject.toml` con: `fastapi`, `uvicorn`, `langgraph`, `langchain`, `chromadb`, `pydantic`, `pydantic-settings`, `pytest`
- [x] Crear `.env.example` (API key del LLM, rutas) y `app/core/config.py`
- [x] Endpoint `GET /health` en FastAPI y primer test con pytest
- [x] Definir los esquemas Pydantic (`TailoredCV`, `ProjectSummary`, `CoverLetter`, `JobAnalysis`)

### Fase 2: RAG
- [x] Script de ingesta (carga md/txt/pdf + chunking por encabezados + embeddings Gemini + ChromaDB, idempotente por archivo)
- [x] Retriever con metadatos (tipo de documento: cv/project/certification/other)
- [x] Tests del RAG (embedder falso, sin red)

### Fase 3: Agente
- [x] Estado y nodos de LangGraph (`analyze_job` → `retrieve` → `generate` → `validate`) en `app/agent/`
- [x] Reintentos (máx. 3) ante salida inválida o `evidence_ids` inventados, con feedback al LLM
- [x] Fallback `insufficient_evidence` si el perfil no respalda la oferta
- [x] Tests del agente con LLM falso (sin red)

### Fase 4: API y calidad
- [x] Endpoints `POST /ingest`, `GET /sources`, `DELETE /sources/{name}` y `POST /generate`
- [x] Rate limiting (slowapi), logs JSON estructurados con `X-Request-ID`, manejo de errores (413/415/422/429/502/503)
- [x] Tests de endpoints (29 tests en total)
- [x] Set de evaluación de 20 ejemplos (`eval/cases.json`, 15 con encaje y 5 sin encaje) y script `python -m scripts.evaluate`
- [x] Umbral `MIN_TOP_SCORE` calibrado con la evaluación (las ofertas sin encaje pasaron de 0/5 a 5/5 rechazadas)

#### Resultados de evaluación

Modelo `gemini-3.1-flash-lite`, embeddings `gemini-embedding-001`. Reporte completo en `eval/report.md`.

| Métrica | Resultado |
|---|---|
| Recall@5 (evidencia esperada en el top 5) | 31/32 (97%) |
| Documento generado con éxito (ofertas con encaje) | 15/15 (100%) |
| Validación Pydantic al primer intento | 15/15 (100%) |
| Fidelidad (sin cifras inventadas) | 15/15 (100%) |
| Rechazos correctos (ofertas sin encaje) | 5/5 (100%) |
| Falsos rechazos (ofertas con encaje) | 0/15 (0%) |

> Limitaciones: el perfil es pequeño (16 fragmentos), así que Recall@5 es permisivo, y el umbral se calibró con los mismos 20 casos. Para comparar modelos: `python -m scripts.evaluate --model gemini-3.5-flash`.

### Fase 4b: Frontend
- [x] `app/static/index.html` con Bootstrap 5 (HTML, CSS y JS separados; modo claro/oscuro)
- [x] `app.js` con `fetch` a `/generate`, `/ingest` y `/sources`, estados de carga y errores
- [x] Mostrar evidencias usadas, copiar y descargar resultado (.md)
- [x] Montar `StaticFiles` en FastAPI (la UI se abre en `http://localhost:8000`)
- [x] Selector de modelo Gemini (`GET /models`, hasta 10 modelos; `python -m scripts.list_models` los lista por consola)
- [x] Aviso de privacidad obligatorio: sin aceptarlo la UI queda bloqueada y la API responde 403 (cabecera `X-Privacy-Consent`)

## Posibles mejoras futuras

El despliegue público no forma parte de la versión completada y no está comprometido como siguiente fase. Si se decide publicar una demo, estos serían temas a evaluar:

- **Aislamiento y privacidad:** diseñar un modo demo que no exponga ni permita modificar perfiles de otros usuarios; revisar también el aviso de privacidad para ese uso.
- **Control de cuota:** limitar las generaciones globales además del rate limit por IP, o evaluar BYOK para que cada persona use su propia API key de Gemini.
- **Despliegue:** preparar Docker y elegir un proveedor, teniendo en cuenta la persistencia del índice vectorial y las limitaciones de los planes gratuitos.

## Licencia

MIT
