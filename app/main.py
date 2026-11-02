import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from google.genai.errors import APIError
from langchain_google_genai._common import GoogleGenerativeAIError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import generate, health, models, profile
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import setup_logging

setup_logging(settings.log_level)
logger = logging.getLogger("app")
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Agente de Portafolio Adaptativo")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error", extra={"request_id": request_id, "path": request.url.path})
        return JSONResponse({"detail": "Error interno del servidor"}, status_code=500)
    logger.info(
        "request",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": round((time.perf_counter() - start) * 1000),
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError):
    logger.error("service unavailable", extra={"error": str(exc)})
    return JSONResponse({"detail": str(exc)}, status_code=503)


async def upstream_error_handler(request: Request, exc: Exception):
    logger.error("upstream llm error", extra={"error": str(exc)[:200]})
    return JSONResponse(
        {"detail": "El modelo de IA está saturado o no responde. Inténtalo de nuevo en unos segundos."},
        status_code=503,
    )


app.add_exception_handler(APIError, upstream_error_handler)
app.add_exception_handler(GoogleGenerativeAIError, upstream_error_handler)


app.include_router(health.router)
app.include_router(models.router)
app.include_router(profile.router)
app.include_router(generate.router)

# Debe montarse al final para no tapar las rutas de la API
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
