"""
DARA — Qdrant Vector Store Client
Manages two collections:
  - code_embeddings: AST-chunked code snippets with metadata
  - error_embeddings: Historical error signatures for similar-bug lookup
"""
from __future__ import annotations

import logging
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from config.settings import get_settings

logger = logging.getLogger(__name__)

VECTOR_DIM = 768          # all-mpnet-base-v2 output dimension
CODE_COLLECTION = "code_embeddings"
ERROR_COLLECTION = "error_embeddings"


class QdrantStore:
    """
    Async Qdrant wrapper. Handles collection setup, upserts, and typed searches.
    """

    def __init__(self, url: str) -> None:
        self._client = AsyncQdrantClient(url=url)

    async def initialize(self) -> None:
        """Ensure both required collections exist with correct vector config."""
        response = await self._client.get_collections()
        existing = {c.name for c in response.collections}

        if CODE_COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=CODE_COLLECTION,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection", extra={"collection": CODE_COLLECTION})

        if ERROR_COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=ERROR_COLLECTION,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection", extra={"collection": ERROR_COLLECTION})

    async def close(self) -> None:
        await self._client.close()

    # ─── Code Chunk Operations ───────────────────────────────

    async def upsert_code_chunk(
        self,
        chunk_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """
        Upsert a code chunk into the code_embeddings collection.
        payload should include: file_path, function_name, language, service, line_start, line_end, content
        """
        await self._client.upsert(
            collection_name=CODE_COLLECTION,
            points=[PointStruct(id=chunk_id, vector=vector, payload=payload)],
        )

    async def search_similar_code(
        self,
        query_vector: list[float],
        service: str | None = None,
        language: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Search for code chunks similar to the query vector.
        Optional filtering by service and/or language.
        """
        must_conditions = []
        if service:
            must_conditions.append(
                FieldCondition(key="service", match=MatchValue(value=service))
            )
        if language:
            must_conditions.append(
                FieldCondition(key="language", match=MatchValue(value=language))
            )

        results = await self._client.query_points(
            collection_name=CODE_COLLECTION,
            query=query_vector,
            query_filter=Filter(must=must_conditions) if must_conditions else None,
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "score": point.score,
                "chunk_id": str(point.id),
                **point.payload,  # type: ignore
            }
            for point in results.points
        ]

    async def delete_service_chunks(self, service: str) -> None:
        """Remove all code chunks belonging to a service (called on repo rebuild)."""
        from qdrant_client.models import FilterSelector
        await self._client.delete(
            collection_name=CODE_COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="service", match=MatchValue(value=service))]
                )
            ),
        )

    # ─── Error Embedding Operations ──────────────────────────

    async def upsert_error_embedding(
        self,
        error_id: str,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """
        Store an error embedding for similarity lookup.
        payload: error_class, message_summary, service, outcome, fix_id
        """
        await self._client.upsert(
            collection_name=ERROR_COLLECTION,
            points=[PointStruct(id=error_id, vector=vector, payload=payload)],
        )

    async def search_similar_errors(
        self,
        query_vector: list[float],
        error_class: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """
        Find historically similar errors that were successfully resolved.
        Only returns errors with outcome='accepted'.
        """
        must_conditions = [
            FieldCondition(key="outcome", match=MatchValue(value="accepted"))
        ]
        if error_class:
            must_conditions.append(
                FieldCondition(key="error_class", match=MatchValue(value=error_class))
            )

        results = await self._client.query_points(
            collection_name=ERROR_COLLECTION,
            query=query_vector,
            query_filter=Filter(must=must_conditions),
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "score": point.score,
                "error_id": str(point.id),
                **point.payload,  # type: ignore
            }
            for point in results.points
        ]

    async def update_error_outcome(self, error_id: str, outcome: str) -> None:
        """Update the outcome field on an error embedding after fix review."""
        await self._client.set_payload(
            collection_name=ERROR_COLLECTION,
            payload={"outcome": outcome},
            points=[error_id],
        )

    async def set_payload_fields(self, error_id: str, fields: dict) -> None:
        """
        Update arbitrary payload fields on an error embedding.
        Used by RLHF feedback processor to set:
          outcome, reliability_score, human_merged, human_rejected, human_feedback, fix_id
        """
        await self._client.set_payload(
            collection_name=ERROR_COLLECTION,
            payload=fields,
            points=[error_id],
        )


_qdrant_instance: QdrantStore | None = None


def get_qdrant() -> QdrantStore:
    global _qdrant_instance
    if _qdrant_instance is None:
        _qdrant_instance = QdrantStore(get_settings().qdrant_url)
    return _qdrant_instance
