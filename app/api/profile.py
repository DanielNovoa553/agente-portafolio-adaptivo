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
    sources = store.list_sources()
    return SourcesResponse(sources=sources, total_chunks=sum(sources.values()))


@router.delete("/sources/{name}", status_code=204)
def delete_source(name: str, store: ProfileStore = Depends(store_dep)):
    if name not in store.list_sources():
        raise HTTPException(404, "Fuente no encontrada")
    store.delete_source(name)
