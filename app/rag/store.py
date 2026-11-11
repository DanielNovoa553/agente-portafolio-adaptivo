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
    """Crea y cachea la función de embeddings de Google para ChromaDB.

    Returns:
        Función de embeddings configurada con el modelo y la clave de la aplicación.

    Raises:
        RuntimeError: Si falta `GOOGLE_API_KEY` en la configuración.
    """
    if not settings.google_api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env para generar embeddings")
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    return GoogleGenaiEmbeddingFunction(
        model_name=settings.embedding_model, api_key_env_var="GOOGLE_API_KEY"
    )


class ProfileStore:
    """Almacén de fragmentos del perfil respaldado por una colección de ChromaDB."""

    def __init__(self, path: str | None = None, embedding_function=None):
        """Inicializa el almacén y su cliente de ChromaDB.

        Args:
            path: Ruta del almacenamiento persistente; si es `None` se usa la
                configuración y si es vacía se crea un cliente efímero.
            embedding_function: Función de embeddings opcional; si falta se usa
                la de Google configurada por defecto.
        """
        if path is None:
            path = settings.chroma_path
        self._client = (
            chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        )
        self._embedder = embedding_function
        self._collection = None

    @property
    def collection(self):
        """Devuelve la colección de ChromaDB, creándola de forma perezosa.

        Returns:
            Colección `profile` configurada con similitud coseno.

        Raises:
            RuntimeError: Si no hay clave de Google y no se proporcionó un embedder.
        """
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION,
                embedding_function=self._embedder or _default_embedder(),
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def add_document(self, source: str, doc_type: DocType, chunks: list[Chunk]) -> int:
        """Reemplaza todos los fragmentos previos de `source` (ingesta idempotente).

        Args:
            source: Nombre del archivo que se indexa.
            doc_type: Tipo de documento asignado a todos los fragmentos.
            chunks: Fragmentos que se van a almacenar.

        Returns:
            Número de fragmentos indexados.
        """
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
        """Elimina todos los fragmentos asociados a un archivo.

        Args:
            source: Nombre del archivo cuyos fragmentos se eliminan.
        """
        self.collection.delete(where={"source": source})

    def list_sources(self) -> dict[str, int]:
        """Cuenta los fragmentos indexados por archivo de origen.

        Returns:
            Diccionario que asocia cada nombre de archivo con su número de fragmentos.
        """
        metas = self.collection.get(include=["metadatas"])["metadatas"] or []
        counts: dict[str, int] = {}
        for m in metas:
            counts[m["source"]] = counts.get(m["source"], 0) + 1
        return counts

    def count(self) -> int:
        """Cuenta el total de fragmentos almacenados.

        Returns:
            Número de fragmentos de la colección.
        """
        return self.collection.count()

    def reset(self) -> None:
        """Elimina la colección completa y olvida la instancia cacheada."""
        try:
            self._client.delete_collection(COLLECTION)
        except Exception:
            pass
        self._collection = None

    def retrieve(
        self, query: str, k: int = 5, doc_type: DocType | None = None
    ) -> list[Evidence]:
        """Recupera los fragmentos más similares a una consulta.

        Args:
            query: Texto de la consulta.
            k: Número máximo de fragmentos a devolver.
            doc_type: Si se indica, restringe la búsqueda a ese tipo de documento.

        Returns:
            Evidencias ordenadas por similitud; lista vacía si no hay fragmentos.
        """
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
    """Devuelve la instancia compartida del almacén del perfil.

    Returns:
        `ProfileStore` persistente cacheado durante la vida del proceso.
    """
    return ProfileStore()
