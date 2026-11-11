from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.api.deps import consent_dep, store_dep
from app.core.config import settings
from app.core.limiter import limiter
from app.rag.ingest import ingest_file
from app.rag.loaders import UnsupportedFileError
from app.rag.store import ProfileStore
from app.schemas.api import IngestResponse, SourcesResponse
from app.schemas.evidence import DocType

router = APIRouter(tags=["profile"], dependencies=[Depends(consent_dep)])


@router.post("/ingest", response_model=IngestResponse)
@limiter.limit(settings.ingest_rate_limit)
async def ingest(
    request: Request,
    file: UploadFile = File(...),
    doc_type: DocType = Form(DocType.other),
    store: ProfileStore = Depends(store_dep),
):
    """Indexa un archivo del perfil en el almacén vectorial.

    Args:
        request: Petición HTTP, requerida por el limitador de tasa.
        file: Archivo subido (.md, .txt o .pdf).
        doc_type: Tipo de documento para clasificar los fragmentos.
        store: Almacén del perfil donde se indexa el contenido.

    Returns:
        IngestResponse: Nombre de la fuente, tipo y número de fragmentos indexados.

    Raises:
        HTTPException: 400 si el archivo no tiene nombre, 403 sin consentimiento,
            413 si supera el tamaño máximo, 415 si el formato no está soportado,
            422 si faltan campos o el tipo de documento no es válido, 429 si se
            supera el límite y 503 si falta la API key para los embeddings.
    """
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, f"Archivo mayor a {settings.max_upload_bytes // 1_000_000} MB")
    name = PurePath(file.filename or "").name
    if not name:
        raise HTTPException(400, "El archivo necesita un nombre")
    try:
        chunks = ingest_file(store, name, content, doc_type)
    except UnsupportedFileError as exc:
        raise HTTPException(415, str(exc)) from exc
    return IngestResponse(source=name, doc_type=doc_type, chunks=chunks)


@router.get("/sources", response_model=SourcesResponse)
def list_sources(store: ProfileStore = Depends(store_dep)):
    """Lista las fuentes indexadas y el total de fragmentos.

    Args:
        store: Almacén del perfil consultado.

    Returns:
        SourcesResponse: Fuentes con su número de fragmentos y el total.

    Raises:
        HTTPException: 403 sin consentimiento y 503 si falta la API key para ChromaDB.
    """
    sources = store.list_sources()
    return SourcesResponse(sources=sources, total_chunks=sum(sources.values()))


@router.delete("/sources/{name}", status_code=204)
def delete_source(name: str, store: ProfileStore = Depends(store_dep)):
    """Elimina una fuente del perfil y todos sus fragmentos.

    Args:
        name: Nombre de la fuente a eliminar.
        store: Almacén del perfil del que se borra la fuente.

    Raises:
        HTTPException: 403 sin consentimiento, 404 si la fuente no existe y 503 si
            falta la API key para acceder a ChromaDB.
    """
    if name not in store.list_sources():
        raise HTTPException(404, "Fuente no encontrada")
    store.delete_source(name)
