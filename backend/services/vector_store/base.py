"""VectorStore protocol. Payload matches the Qdrant point; chunk text stays in Mongo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence


@dataclass(frozen=True)
class VectorPoint:
    point_id: str
    vector: tuple[float, ...]
    repo_id: str
    chunk_id: str
    file_path: str
    path_prefixes: tuple[str, ...]
    language: str
    start_line: int
    end_line: int
    commit_hash: str
    function_name: Optional[str] = None
    class_name: Optional[str] = None


@dataclass(frozen=True)
class SearchHit:
    point_id: str
    score: float
    repo_id: str
    chunk_id: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    commit_hash: str
    function_name: Optional[str] = None
    class_name: Optional[str] = None


class VectorStore(Protocol):
    async def ensure_collection(self) -> None: ...

    async def upsert(self, points: Sequence[VectorPoint]) -> None: ...

    async def search(
        self,
        vector: Sequence[float],
        *,
        repo_id: str,
        top_k: int = 15,
        path_prefix: Optional[str] = None,
        language: Optional[str] = None,
    ) -> list[SearchHit]:
        """Return cosine scores in [0, 1], highest first."""
        ...

    async def delete_by_path(self, repo_id: str, file_path: str) -> None: ...

    async def delete_by_repo(self, repo_id: str) -> None: ...

    async def delete_ids(self, point_ids: Sequence[str]) -> None:
        """Drop specific points. Missing ids are ignored."""
        ...

    async def retrieve(self, point_ids: Sequence[str]) -> dict[str, list[float]]:
        """point_id -> dense vector. Missing ids omitted."""
        ...

    async def ping(self) -> None: ...
