"""In-memory VectorStore for tests and local runs without Qdrant."""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from services.embeddings.base import check_vector
from services.vector_store.base import SearchHit, VectorPoint


class MemoryVectorStore:
    def __init__(self) -> None:
        self._points: dict[str, VectorPoint] = {}

    async def ensure_collection(self) -> None:
        return None

    async def ping(self) -> None:
        return None

    async def upsert(self, points: Sequence[VectorPoint]) -> None:
        for point in points:
            check_vector(point.vector)
        for point in points:
            self._points[point.point_id] = point

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
        hits: list[SearchHit] = []
        for point in self._points.values():
            if point.repo_id != repo_id:
                continue
            if language is not None and point.language != language:
                continue
            if path_prefix is not None and path_prefix not in point.path_prefixes:
                continue
            score = _cosine(query, np.asarray(point.vector, dtype=np.float64))
            hits.append(
                SearchHit(
                    point_id=point.point_id,
                    score=score,
                    repo_id=point.repo_id,
                    chunk_id=point.chunk_id,
                    file_path=point.file_path,
                    language=point.language,
                    function_name=point.function_name,
                    class_name=point.class_name,
                    start_line=point.start_line,
                    end_line=point.end_line,
                    commit_hash=point.commit_hash,
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.point_id))
        return hits[:top_k]

    async def delete_by_path(self, repo_id: str, file_path: str) -> None:
        drop = [
            pid
            for pid, point in self._points.items()
            if point.repo_id == repo_id and point.file_path == file_path
        ]
        for pid in drop:
            del self._points[pid]

    async def delete_by_repo(self, repo_id: str) -> None:
        drop = [pid for pid, point in self._points.items() if point.repo_id == repo_id]
        for pid in drop:
            del self._points[pid]


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    return max(0.0, min(1.0, float(np.dot(left, right) / denom)))
