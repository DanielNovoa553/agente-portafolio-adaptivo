from enum import Enum

from pydantic import BaseModel, Field


class DocType(str, Enum):
    cv = "cv"
    project = "project"
    certification = "certification"
    other = "other"


class Evidence(BaseModel):
    id: str
    text: str
    doc_type: DocType
    source: str
    section: str = ""
    score: float = Field(description="Similitud (1 = idéntico)")
