import os
from functools import lru_cache

import chromadb
from chromadb.utils.embedding_functions import GoogleGenaiEmbeddingFunction

from app.core.config import settings
from app.rag.chunking import Chunk
from app.schemas.evidence import DocType, Evidence

COLLECTION = "profile"


@lru_cache
def _default_embedder():
    if not settings.google_api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env para generar embeddings")
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    return GoogleGenaiEmbeddingFunction(
        model_name=settings.embedding_model, api_key_env_var="GOOGLE_API_KEY"
    )


class ProfileStore:
    def __init__(self, path: str | None = None, embedding_function=None):
        if path is None:
            path = settings.chroma_path
        self._client = (
            chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        )
        self._embedder = embedding_function
        self._collection = None

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION,
                embedding_function=self._embedder or _default_embedder(),
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add_document(self, source: str, doc_type: DocType, chunks: list[Chunk]) -> int:
        """Reemplaza todos los fragmentos previos de `source` (ingesta idempotente)."""
        self.delete_source(source)
        if not chunks:
            return 0
        self.collection.add(
            ids=[f"{source}::{i}" for i in range(len(chunks))],
            documents=[c.text for c in chunks],
            metadatas=[
                {"source": source, "doc_type": doc_type.value, "section": c.section}
                for c in chunks
            ],
        )
        return len(chunks)

    def delete_source(self, source: str) -> None:
        self.collection.delete(where={"source": source})

    def list_sources(self) -> dict[str, int]:
        metas = self.collection.get(include=["metadatas"])["metadatas"] or []
        counts: dict[str, int] = {}
        for m in metas:
            counts[m["source"]] = counts.get(m["source"], 0) + 1
        return counts

    def count(self) -> int:
        return self.collection.count()

    def reset(self) -> None:
        try:
            self._client.delete_collection(COLLECTION)
        except Exception:
            pass
        self._collection = None

    def retrieve(
        self, query: str, k: int = 5, doc_type: DocType | None = None
    ) -> list[Evidence]:
        total = self.count()
        if total == 0:
            return []
        res = self.collection.query(
            query_texts=[query],
            n_results=min(k, total),
            where={"doc_type": doc_type.value} if doc_type else None,
        )
        return [
            Evidence(
                id=id_,
                text=doc,
                doc_type=DocType(meta["doc_type"]),
                source=meta["source"],
                section=meta.get("section", ""),
                score=round(1 - dist, 4),
            )
            for id_, doc, meta, dist in zip(
                res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
            )
        ]


@lru_cache
def get_store() -> ProfileStore:
    return ProfileStore()
