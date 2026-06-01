"""
RagStore — ChromaDB + nomic-embed-text (Ollama) for per-topic semantic recall.

Gracefully disabled if chromadb is not installed or Ollama is unreachable.
Namespaces: memory | projects | obsidian (operators can customize)

Ported from claude-brain. Ollama endpoint is configurable.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("nexus.rag_store")

DEFAULT_DB_PATH = Path.home() / ".local" / "nexus" / "rag-db"
DEFAULT_OLLAMA_BASE = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_NAMESPACES = ("memory", "projects", "obsidian")
RELEVANCE_THRESHOLD = 0.45  # cosine distance — lower = more similar


class RagStore:
    """
    Thin ChromaDB wrapper for semantic recall.

    Instantiate once at startup; call query() per message.
    Gracefully disabled if chromadb is not installed or Ollama is unreachable.

    Args:
        db_path: Path to ChromaDB persistence directory.
        ollama_base: Ollama API base URL.
        embed_model: Ollama embedding model name.
        namespaces: Collection names to create and query.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        ollama_base: str = DEFAULT_OLLAMA_BASE,
        embed_model: str = DEFAULT_EMBED_MODEL,
        namespaces: tuple[str, ...] = DEFAULT_NAMESPACES,
    ):
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._ollama_base = ollama_base.rstrip("/")
        self._embed_model = embed_model
        self._namespaces = namespaces
        self._client = None
        self._collections: dict = {}
        self._available = False
        self._init()

    def _init(self) -> None:
        try:
            import chromadb  # type: ignore

            self._client = chromadb.PersistentClient(path=str(self._db_path))
            for ns in self._namespaces:
                self._collections[ns] = self._client.get_or_create_collection(
                    name=ns,
                    metadata={"hnsw:space": "cosine"},
                )
            self._available = True
            logger.info(f"RagStore ready at {self._db_path} (model={self._embed_model})")
        except Exception as exc:
            logger.warning(f"RagStore disabled — init failed: {exc}")

    # ── Embedding ─────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        resp = httpx.post(
            f"{self._ollama_base}/api/embeddings",
            json={"model": self._embed_model, "prompt": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    # ── Ingest ────────────────────────────────────────────────────────

    def ingest(self, docs: list[dict], namespace: str) -> int:
        """
        Upsert docs into namespace. Each doc: {id, text, metadata}.
        Returns count of docs successfully embedded and upserted.
        """
        if not self._available or namespace not in self._collections:
            return 0

        col = self._collections[namespace]
        ids, embeddings, texts, metadatas = [], [], [], []

        for doc in docs:
            try:
                emb = self._embed(doc["text"])
                ids.append(doc["id"])
                embeddings.append(emb)
                texts.append(doc["text"])
                metadatas.append(doc.get("metadata", {}))
            except Exception as exc:
                logger.warning(f"Embed failed for {doc.get('id', '?')}: {exc}")

        if ids:
            col.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)

        return len(ids)

    # ── Query ─────────────────────────────────────────────────────────

    def query(
        self,
        text: str,
        namespaces: Optional[list[str]] = None,
        n_results: int = 4,
    ) -> list[str]:
        """
        Return up to n_results relevant text chunks across specified namespaces.
        Results are deduplicated and filtered by cosine distance threshold.
        """
        if not self._available or not text.strip():
            return []

        targets = [ns for ns in (namespaces or list(self._namespaces)) if ns in self._collections]

        try:
            emb = self._embed(text)
        except Exception as exc:
            logger.warning(f"RagStore query embed failed: {exc}")
            return []

        seen: set[str] = set()
        results: list[tuple[float, str]] = []

        for ns in targets:
            col = self._collections[ns]
            try:
                count = col.count()
                if count == 0:
                    continue
                n = min(n_results, count)
                res = col.query(
                    query_embeddings=[emb],
                    n_results=n,
                    include=["documents", "distances"],
                )
                docs = res.get("documents", [[]])[0]
                dists = res.get("distances", [[]])[0]
                for doc, dist in zip(docs, dists):
                    if dist < RELEVANCE_THRESHOLD and doc not in seen:
                        seen.add(doc)
                        results.append((dist, doc))
            except Exception as exc:
                logger.warning(f"RagStore query failed in namespace {ns!r}: {exc}")

        results.sort(key=lambda x: x[0])
        return [t for _, t in results[:n_results]]

    # ── Status ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    def collection_counts(self) -> dict[str, int]:
        if not self._available:
            return {}
        return {ns: col.count() for ns, col in self._collections.items()}
