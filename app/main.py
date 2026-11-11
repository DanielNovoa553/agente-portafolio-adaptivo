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

from app.api import generate, health, interview, models, profile
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
    """Registra cada petición, añade `X-Request-ID` y captura errores no controlados.

    Args:
        request: Petición HTTP entrante.
        call_next: Función que continúa la cadena de middleware y ejecuta la ruta.

    Returns:
        Respuesta HTTP con la cabecera `X-Request-ID`; si la ruta falla, una
        respuesta 500 con un mensaje genérico.
    """
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
    """Traduce un `RuntimeError` del servicio en una respuesta 503.

    Args:
        request: Petición que provocó el error.
        exc: Excepción capturada; su mensaje se devuelve como detalle.

    Returns:
        Respuesta JSON 503 con el mensaje de la excepción.
    """
    logger.error("service unavailable", extra={"error": str(exc)})
    return JSONResponse({"detail": str(exc)}, status_code=503)


async def upstream_error_handler(request: Request, exc: Exception):
    """Traduce errores del proveedor del LLM en una respuesta 503 amigable.

    Args:
        request: Petición que provocó el error.
        exc: Excepción del proveedor; solo se registra un extracto.

    Returns:
        Respuesta JSON 503 con un mensaje para reintentar más tarde.
    """
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
app.include_router(interview.router)

# Debe montarse al final para no tapar las rutas de la API
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
