"""Qdrant VectorStore. Dense 768 cosine plus an unused sparse BM25 slot."""

from __future__ import annotations

from typing import Optional, Sequence

from qdrant_client import AsyncQdrantClient, models

from config.settings import settings
from services.embeddings.base import EMBEDDING_DIM, check_vector
from services.vector_store.base import SearchHit, VectorPoint

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
_TIMEOUT_S = 30
_RETRIEVE_BATCH = 256
_UPSERT_BATCH = 128
_PAYLOAD_INDEXES = ("file_path", "path_prefixes", "language", "commit_hash")


class QdrantVectorStore:
    def __init__(
        self,
        client: Optional[AsyncQdrantClient] = None,
        collection: Optional[str] = None,
    ) -> None:
        self.collection = collection or settings.qdrant_collection
        if client is not None:
            self._client = client
            self._owns_client = False
            return
        if not settings.qdrant_url:
            raise RuntimeError("QDRANT_URL is not configured")
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=_TIMEOUT_S,
        )
        self._owns_client = True

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def ping(self) -> None:
        await self._client.get_collections()

    async def ensure_collection(self) -> None:
        if not await self._client.collection_exists(self.collection):
            await self._client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=EMBEDDING_DIM, distance=models.Distance.COSINE
                    ),
                },
                sparse_vectors_config={
                    SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF),
                },
            )
        await self._client.create_payload_index(
            collection_name=self.collection,
            field_name="repo_id",
            field_schema=models.KeywordIndexParams(
                type=models.PayloadSchemaType.KEYWORD, is_tenant=True
            ),
        )
        for field in _PAYLOAD_INDEXES:
            await self._client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    async def upsert(self, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        structs = []
        for point in points:
            check_vector(point.vector)
            structs.append(
                models.PointStruct(
                    id=point.point_id,
                    vector={DENSE_VECTOR: list(point.vector)},
                    payload=_payload(point),
                )
            )
        for i in range(0, len(structs), _UPSERT_BATCH):
            await self._client.upsert(
                collection_name=self.collection,
                points=structs[i : i + _UPSERT_BATCH],
            )

    async def search(
        self,
        vector: Sequence[float],
        *,
        repo_id: str,
        top_k: int = 15,
        path_prefix: Optional[str] = None,
        language: Optional[str] = None,
    ) -> list[SearchHit]:
        query = check_vector(vector)
        if top_k <= 0:
            return []
        result = await self._client.query_points(
            collection_name=self.collection,
            query=query.tolist(),
            using=DENSE_VECTOR,
            query_filter=_filter(
                repo_id=repo_id, path_prefix=path_prefix, language=language
            ),
            limit=top_k,
            with_payload=True,
        )
        return [_hit(scored) for scored in result.points]

    async def delete_by_path(self, repo_id: str, file_path: str) -> None:
        await self._client.delete(
            collection_name=self.collection,
            points_selector=_filter(repo_id=repo_id, file_path=file_path),
        )

    async def delete_by_repo(self, repo_id: str) -> None:
        await self._client.delete(
            collection_name=self.collection,
            points_selector=_filter(repo_id=repo_id),
        )

    async def delete_ids(self, point_ids: Sequence[str]) -> None:
        """Delete listed point ids in batches. Missing ids are ignored."""
        ids = list(point_ids)
        for i in range(0, len(ids), _RETRIEVE_BATCH):
            batch = ids[i : i + _RETRIEVE_BATCH]
            if not batch:
                continue
            await self._client.delete(
                collection_name=self.collection,
                points_selector=models.PointIdsList(points=batch),
            )

    async def retrieve(self, point_ids: Sequence[str]) -> dict[str, list[float]]:
        """Fetch named dense vectors. Missing ids are omitted."""
        out: dict[str, list[float]] = {}
        ids = list(point_ids)
        for i in range(0, len(ids), _RETRIEVE_BATCH):
            batch = ids[i : i + _RETRIEVE_BATCH]
            if not batch:
                continue
            records = await self._client.retrieve(
                collection_name=self.collection,
                ids=batch,
                with_payload=False,
                with_vectors=[DENSE_VECTOR],
            )
            for record in records:
                dense = _dense_vector(record.vector)
                if dense is not None:
                    out[str(record.id)] = dense
        return out


def _dense_vector(vector) -> Optional[list[float]]:
    if vector is None:
        return None
    if isinstance(vector, dict):
        dense = vector.get(DENSE_VECTOR)
        return list(dense) if dense is not None else None
    return list(vector)


def _payload(point: VectorPoint) -> dict:
    return {
        "repo_id": point.repo_id,
        "chunk_id": point.chunk_id,
        "file_path": point.file_path,
        "path_prefixes": list(point.path_prefixes),
        "language": point.language,
        "function_name": point.function_name,
        "class_name": point.class_name,
        "start_line": point.start_line,
        "end_line": point.end_line,
        "commit_hash": point.commit_hash,
    }


def _filter(
    *,
    repo_id: str,
    file_path: Optional[str] = None,
    path_prefix: Optional[str] = None,
    language: Optional[str] = None,
) -> models.Filter:
    must = [
        models.FieldCondition(key="repo_id", match=models.MatchValue(value=repo_id)),
    ]
    if file_path is not None:
        must.append(
            models.FieldCondition(
                key="file_path", match=models.MatchValue(value=file_path)
            )
        )
    if path_prefix is not None:
        must.append(
            models.FieldCondition(
                key="path_prefixes", match=models.MatchValue(value=path_prefix)
            )
        )
    if language is not None:
        must.append(
            models.FieldCondition(
                key="language", match=models.MatchValue(value=language)
            )
        )
    return models.Filter(must=must)


def _hit(scored: models.ScoredPoint) -> SearchHit:
    payload = scored.payload or {}
    return SearchHit(
        point_id=str(scored.id),
        score=max(0.0, min(1.0, float(scored.score))),
        repo_id=payload["repo_id"],
        chunk_id=payload["chunk_id"],
        file_path=payload["file_path"],
        language=payload["language"],
        start_line=int(payload["start_line"]),
        end_line=int(payload["end_line"]),
        commit_hash=payload["commit_hash"],
        function_name=payload.get("function_name"),
        class_name=payload.get("class_name"),
    )
