"""Deterministic Qdrant point ids and payload path prefixes."""

from __future__ import annotations

import uuid

POINT_NAMESPACE = uuid.UUID("a6a7e0d0-5c5e-4e11-9c0d-5e115e000001")


def make_point_id(repo_id: str, chunk_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, f"{repo_id}:{chunk_id}"))


def path_prefixes(file_path: str) -> list[str]:
    """Ancestor directories of a repo-relative path. 'a/b/c.py' → ['a', 'a/b']."""
    parts = [p for p in file_path.replace("\\", "/").strip("/").split("/") if p]
    if len(parts) < 2:
        return []
    prefixes: list[str] = []
    acc: list[str] = []
    for part in parts[:-1]:
        acc.append(part)
        prefixes.append("/".join(acc))
    return prefixes
