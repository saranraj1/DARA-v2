"""DARA - Semantic code retriever with composite re-ranking and token budget"""
from __future__ import annotations
import logging
import tiktoken
from config.settings import get_settings
from storage.qdrant_client import QdrantStore

logger = logging.getLogger(__name__)
_TOKENIZER = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(_TOKENIZER.encode(text))

class ContextRetriever:
    def __init__(self, qdrant: QdrantStore, llm_router=None):
        self._qdrant = qdrant
        self._llm = llm_router
        self._settings = get_settings()

    async def retrieve_relevant_code(self, query: str, error_file: str | None = None,
                                      service: str | None = None, token_budget: int = 4000,
                                      limit: int = 10) -> list[dict]:
        """Retrieve + re-rank code chunks within token budget."""
        vector = await self._embed(query)
        try:
            raw = await self._qdrant.search_similar_code(
                query_vector=vector, service=service, limit=limit * 2)
        except Exception as e:
            logger.warning("Qdrant code search failed: %s", e)
            return []
        if not raw:
            return []
        # Composite re-rank: 70% vector sim, 20% same-file, 10% same-service
        scored = sorted(raw, key=lambda c: self._score(c, error_file, service), reverse=True)
        results, used = [], 0
        for chunk in scored[:limit]:
            t = count_tokens(chunk.get("content", ""))
            if used + t > token_budget:
                break
            results.append(chunk)
            used += t
        logger.debug("Retrieved %d chunks, %d/%d tokens used", len(results), used, token_budget)
        return results

    async def retrieve_similar_errors(self, error_summary: str, error_class: str | None = None,
                                       limit: int = 5) -> list[dict]:
        """Retrieve historically resolved errors for fix template matching."""
        vector = await self._embed(error_summary)
        try:
            return await self._qdrant.search_similar_errors(
                query_vector=vector, error_class=error_class, limit=limit)
        except Exception as e:
            logger.warning("Qdrant error search failed: %s", e)
            return []

    async def index_code_chunks(self, chunks: list, service: str) -> int:
        """Embed and store CodeChunk objects in Qdrant. Returns count indexed."""
        if not chunks or not self._llm:
            return 0
        texts = [c.content for c in chunks]
        try:
            vectors = await self._llm.embed_batch(texts)
        except Exception as e:
            logger.error("Batch embed failed: %s", e)
            return 0
        indexed = 0
        import uuid as _uuid
        for chunk, vec in zip(chunks, vectors):
            try:
                # Qdrant requires UUID or integer IDs — derive deterministic UUID from chunk path
                qdrant_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, chunk.chunk_id))
                await self._qdrant.upsert_code_chunk(
                    chunk_id=qdrant_id, vector=vec,
                    payload={"file_path": chunk.file_path, "function_name": chunk.function_name,
                             "class_name": chunk.class_name, "language": chunk.language,
                             "service": service, "line_start": chunk.line_start,
                             "line_end": chunk.line_end, "content": chunk.content,
                             "chunk_id": chunk.chunk_id})   # keep original id in payload
                indexed += 1
            except Exception as e:
                logger.warning("Failed to index chunk %s: %s", chunk.chunk_id, e)
        logger.info("Indexed %d/%d chunks for service=%s", indexed, len(chunks), service)
        return indexed

    async def index_error_embedding(self, error: dict, fix_id: str | None = None) -> None:
        summary = f"{error.get('error_class','')} {error.get('message','')[:200]}"
        vec = await self._embed(summary)
        try:
            await self._qdrant.upsert_error_embedding(
                error_id=error.get("id",""), vector=vec,
                payload={"error_class": error.get("error_class"), "service": error.get("service"),
                         "message_summary": error.get("message","")[:300],
                         "outcome": "accepted" if fix_id else "pending", "fix_id": fix_id or ""})
        except Exception as e:
            logger.warning("Error embedding failed: %s", e)

    async def _embed(self, text: str) -> list[float]:
        if not self._llm:
            return [0.0] * 768
        return await self._llm.embed(text)

    def _score(self, chunk: dict, error_file: str | None, service: str | None) -> float:
        base = float(chunk.get("score", 0.5))
        return (0.7 * base
                + (0.2 if error_file and chunk.get("file_path") == error_file else 0.0)
                + (0.1 if service and chunk.get("service") == service else 0.0))
