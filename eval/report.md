# Reporte de evaluación

Fecha: 2026-11-01 · Modelo: `gemini-3.1-flash-lite` · Embeddings: `gemini-embedding-001` · MIN_SCORE: 0.35 · MIN_TOP_SCORE: 0.665 · Casos: 20 (0 con error)

| Métrica | Resultado |
|---|---|
| Recall@5 (evidencia esperada en el top 5) | 31/32 (97%) |
| Documento generado con éxito (ofertas con encaje) | 15/15 (100%) |
| Validación Pydantic al primer intento | 15/15 (100%) |
| Fidelidad (sin cifras inventadas) | 15/15 (100%) |
| Rechazos correctos (ofertas sin encaje) | 5/5 (100%) |
| Falsos rechazos (ofertas con encaje) | 0/15 (0%) |

## Detalle

| Caso | Estado | Intentos | Evidencias | Mejor score | Recall | Cifras sin respaldo |
|---|---|---|---|---|---|---|
| backend-aws-serverless | ok | 1 | 9 | 0.77 | 2/3 | – |
| iac-cdk-cloud-engineer | ok | 1 | 9 | 0.69 | 2/2 | – |
| ai-engineer-llm-extraction | ok | 1 | 6 | 0.78 | 2/2 | – |
| genai-vertex-responsible-ai | ok | 1 | 6 | 0.72 | 3/3 | – |
| fastapi-api-developer | ok | 1 | 10 | 0.70 | 3/3 | – |
| data-engineer-etl | ok | 1 | 12 | 0.73 | 3/3 | – |
| fullstack-flask-js | ok | 1 | 9 | 0.75 | 3/3 | – |
| healthcare-hl7 | ok | 1 | 11 | 0.70 | 2/2 | – |
| fintech-credit-risk | ok | 1 | 10 | 0.73 | 2/2 | – |
| qa-testing-coverage | ok | 1 | 7 | 0.69 | 1/1 | – |
| backend-performance-db | ok | 1 | 9 | 0.72 | 2/2 | – |
| ecommerce-wordpress | ok | 1 | 11 | 0.70 | 1/1 | – |
| integrations-channel-manager | ok | 1 | 12 | 0.69 | 1/1 | – |
| technical-trainer | ok | 1 | 11 | 0.70 | 2/2 | – |
| aws-developer-certified | ok | 1 | 9 | 0.78 | 2/2 | – |
| no-match-chef | insufficient_evidence | 0 | 12 | 0.60 | – | – |
| no-match-nurse | insufficient_evidence | 0 | 10 | 0.61 | – | – |
| no-match-lawyer | insufficient_evidence | 0 | 11 | 0.59 | – | – |
| no-match-mechanical | insufficient_evidence | 0 | 11 | 0.64 | – | – |
| no-match-sales | insufficient_evidence | 0 | 11 | 0.60 | – | – |
