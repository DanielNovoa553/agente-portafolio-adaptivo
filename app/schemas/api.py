from typing import Any

from pydantic import BaseModel, Field

from app.agent.state import OutputType
from app.schemas.evidence import DocType, Evidence


class GenerateRequest(BaseModel):
    job_text: str = Field(min_length=50, max_length=8000)
    output_type: OutputType


class GenerateResponse(BaseModel):
    output_type: OutputType
    document: dict[str, Any]
    evidence: list[Evidence]
    attempts: int


class IngestResponse(BaseModel):
    source: str
    doc_type: DocType
    chunks: int


class SourcesResponse(BaseModel):
    sources: dict[str, int]
    total_chunks: int
