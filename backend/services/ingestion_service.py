"""Ingestion service to orchestrate the complete pipeline: clone → parse → embed → store."""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse
from bson import ObjectId

import aiofiles
import httpx
from git import Repo
from git.exc import InvalidGitRepositoryError

from config.settings import settings
from schemas import ChunkMetadata, ParsingStats
from services.embedding_service import EmbeddingService
from services.embeddings.base import EMBEDDING_DIM, Embedder
from services.embeddings.hashing import content_hash
from services.jobs.queue import JobCancelled, JobQueue
from services.mongodb_service import MongoDBService
from services.parser_service import ParserService
from services.repository_service import RepositoryService
from services.vector_store.base import VectorPoint, VectorStore
from services.vector_store.ids import make_point_id, path_prefixes

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
EMBED_BATCH = 5


async def resolve_github_default_branch(repo_url: str, branch: Optional[str]) -> str:
    """Return `branch` if set; otherwise GitHub's default_branch. Never assumes main."""
    if branch:
        return branch
    parsed = urlparse(repo_url)
    host = (parsed.netloc or "").lower().split(":")[0]
    parts = parsed.path.strip("/").split("/")
    if host not in {"github.com", "www.github.com"} or len(parts) < 2:
        raise ValueError("branch is required when the GitHub default branch cannot be resolved")
    owner, name = parts[0], parts[1].replace(".git", "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "codesense"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    url = f"https://api.github.com/repos/{owner}/{name}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            default = response.json().get("default_branch")
    except httpx.HTTPError as exc:
        raise ValueError(f"Could not resolve default branch for {owner}/{name}") from exc
    if not isinstance(default, str) or not default.strip():
        raise ValueError(f"GitHub did not return a default branch for {owner}/{name}")
    return default


async def load_reused_vectors(
    vector_store: VectorStore,
    hash_to_point: Dict[str, str],
) -> Dict[str, List[float]]:
    """Load dense vectors from the store for hashes that already have a point_id."""
    if not hash_to_point:
        return {}
    vectors = await vector_store.retrieve(list(hash_to_point.values()))
    return {
        digest: vectors[point_id]
        for digest, point_id in hash_to_point.items()
        if point_id in vectors
    }


async def reuse_or_embed(
    chunks: List[Dict],
    *,
    repo_id: str,
    embedder: Embedder,
    hash_to_vector: Dict[str, List[float]],
    ensure_active: Optional[Callable[[], Awaitable[None]]] = None,
) -> List[Dict]:
    """Assign content_hash/point_id and fill embeddings, embedding only unseen hashes."""
    missing_hashes: List[str] = []
    missing_texts: List[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        text = chunk["code_text"]
        digest = content_hash(text)
        chunk["content_hash"] = digest
        chunk["point_id"] = make_point_id(repo_id, chunk["chunk_id"])
        if digest not in hash_to_vector and digest not in seen:
            seen.add(digest)
            missing_hashes.append(digest)
            missing_texts.append(text)

    for i in range(0, len(missing_texts), EMBED_BATCH):
        if ensure_active is not None:
            await ensure_active()
        batch_texts = missing_texts[i : i + EMBED_BATCH]
        batch_hashes = missing_hashes[i : i + EMBED_BATCH]
        vectors = await embedder.embed(batch_texts)
        if len(vectors) != len(batch_texts):
            raise RuntimeError(
                f"Embedder returned {len(vectors)} vectors for {len(batch_texts)} texts"
            )
        for digest, vector in zip(batch_hashes, vectors):
            if not vector:
                raise RuntimeError("Embedder returned an empty vector")
            hash_to_vector[digest] = vector

    if ensure_active is not None:
        await ensure_active()

    now = datetime.now(timezone.utc)
    model_name = getattr(embedder, "model_name", None)
    for chunk in chunks:
        vector = hash_to_vector.get(chunk["content_hash"])
        chunk["embedding"] = vector
        if vector is not None:
            chunk["embedded_at"] = now
            chunk["embedding_model"] = model_name
            chunk["schema_version"] = SCHEMA_VERSION
    return chunks


def chunks_to_points(
    chunks: Sequence[Dict],
    *,
    repo_id: str,
    commit_hash: str,
) -> List[VectorPoint]:
    """Build Qdrant points from chunks that already have an embedding. No code_text."""
    points: List[VectorPoint] = []
    for chunk in chunks:
        embedding = chunk.get("embedding")
        if not embedding:
            continue
        points.append(
            VectorPoint(
                point_id=chunk["point_id"],
                vector=tuple(embedding),
                repo_id=repo_id,
                chunk_id=chunk["chunk_id"],
                file_path=chunk["file_path"],
                path_prefixes=tuple(path_prefixes(chunk["file_path"])),
                language=chunk["language"],
                start_line=int(chunk["start_line"]),
                end_line=int(chunk["end_line"]),
                commit_hash=commit_hash,
                function_name=chunk.get("function_name"),
                class_name=chunk.get("class_name"),
            )
        )
    return points


async def index_chunks(
    chunks: List[Dict],
    *,
    repo_id: str,
    embedder: Embedder,
    vector_store: VectorStore,
    hash_to_point: Dict[str, str],
    commit_hash: str,
    stale_point_ids: Sequence[str] = (),
    ensure_active: Optional[Callable[[], Awaitable[None]]] = None,
) -> Tuple[List[Dict], int]:
    """Reuse vectors by content_hash, embed misses, upsert Qdrant. Returns (chunks, embedded_count)."""
    reused = await load_reused_vectors(vector_store, hash_to_point)
    filled = await reuse_or_embed(
        chunks,
        repo_id=repo_id,
        embedder=embedder,
        hash_to_vector=reused,
        ensure_active=ensure_active,
    )
    if ensure_active is not None:
        await ensure_active()
    points = chunks_to_points(filled, repo_id=repo_id, commit_hash=commit_hash)
    await vector_store.upsert(points)
    keep = {point.point_id for point in points}
    drop = [pid for pid in dict.fromkeys(stale_point_ids) if pid not in keep]
    if drop:
        await vector_store.delete_ids(drop)
    embedded_count = sum(1 for chunk in filled if chunk.get("embedded_at") is not None)
    return filled, embedded_count


class IngestionService:
    """Service to orchestrate repository ingestion pipeline."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        vector_store: VectorStore,
        job_queue: Optional[JobQueue] = None,
    ):
        """Inject the process Embedder and VectorStore. Chunking stays on EmbeddingService."""
        self.temp_repos_path = Path(settings.temp_repos_path)
        self.temp_repos_path.mkdir(exist_ok=True, parents=True)
        self.parser_service = ParserService()
        self.embedding_service = EmbeddingService()
        self.embedder = embedder
        self.vector_store = vector_store
        self.mongodb_service = MongoDBService()
        self.job_queue = job_queue

    async def _ensure_active(self, request_id: Optional[str]) -> None:
        """Raise JobCancelled if the job row was cancelled."""
        if request_id and self.job_queue and await self.job_queue.is_cancelled(request_id):
            raise JobCancelled()

    async def _progress(self, request_id: Optional[str], **kwargs) -> None:
        """Write progress, aborting if the job was cancelled."""
        await self._ensure_active(request_id)
        if request_id and self.job_queue:
            await self.job_queue.set_progress(request_id, **kwargs)

    @staticmethod
    def _checkout_commit_sync(repo_path: Path, commit: str) -> None:
        """Check out a specific commit in a cloned repo."""
        Repo(repo_path).git.checkout(commit)

    @staticmethod
    def _extract_repo_info(repo_url: str) -> Tuple[str, str]:
        """Return (owner, repo_name) from a GitHub/GitLab URL."""
        parsed = urlparse(repo_url)
        path_parts = parsed.path.strip("/").split("/")

        if len(path_parts) >= 2:
            owner = path_parts[0]
            repo_name = path_parts[1].replace(".git", "")
            return owner, repo_name
        raise ValueError(f"Invalid repository URL format: {repo_url}")

    def _prepare_temp_repo_path(self, repo_name: str, commit_hash: Optional[str] = None) -> Path:
        """
        Prepare temporary directory path for repository clone.
        Uses format: reponame_commithash

        Args:
            repo_name: Repository name
            commit_hash: Optional commit hash (will be fetched later if not provided)

        Returns:
            Path to temporary repository directory
        """
        if commit_hash:
            temp_dir = self.temp_repos_path / f"{repo_name}_{commit_hash}"
        else:
            # If no commit hash yet, use placeholder (will be updated after clone)
            temp_dir = self.temp_repos_path / f"{repo_name}_pending"
        return temp_dir

    async def _get_commit_hash(self, repo_path: Path) -> Optional[str]:
        """
        Get the current commit hash of the repository.

        Args:
            repo_path: Path to the cloned repository

        Returns:
            Commit hash string or None
        """
        try:
            loop = asyncio.get_event_loop()
            commit_hash = await loop.run_in_executor(
                None,
                lambda: self._get_commit_hash_sync(repo_path)
            )
            return commit_hash
        except (InvalidGitRepositoryError, OSError, ValueError) as e:
            logger.warning("Failed to get commit hash: %s", e)
            return None

    @staticmethod
    def _get_commit_hash_sync(repo_path: Path) -> Optional[str]:
        """Synchronous helper to get commit hash."""
        try:
            repo = Repo(repo_path)
            return repo.head.commit.hexsha[:7]
        except (InvalidGitRepositoryError, OSError, ValueError) as e:
            logger.warning("Failed to get commit hash: %s", e)
            return None

    async def _discover_code_files(self, repo_path: Path) -> List[Path]:
        """
        Discover all code files in the repository.

        Args:
            repo_path: Path to the cloned repository

        Returns:
            List of file paths to process
        """
        supported_extensions = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
            ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".php",
            ".swift", ".kt", ".rs", ".scala", ".r", ".m", ".ipynb"
        }

        skip_dirs = {".git", "node_modules", "__pycache__", "venv",
                     ".venv", "env", ".env", "dist", "build", ".pytest_cache"}

        try:
            # Run file discovery in executor to avoid blocking
            loop = asyncio.get_event_loop()
            max_bytes = settings.max_file_size_kb * 1024
            code_files = await loop.run_in_executor(
                None,
                lambda: [
                    file_path
                    for file_path in repo_path.rglob("*")
                    if file_path.is_file()
                    and not any(skip_dir in file_path.parts for skip_dir in skip_dirs)
                    and file_path.suffix.lower() in supported_extensions
                    and file_path.stat().st_size <= max_bytes
                ]
            )

            logger.info("Discovered %d code files in repository", len(code_files))
            return code_files

        except OSError as e:
            logger.error("Error discovering code files: %s", e)
            return []

    async def _parse_files(self, files: List[Path], repo_path: Path) -> List[Dict]:
        """
        Parse code files to extract structures (functions, classes, etc.).

        Args:
            files: List of file paths to parse
            repo_path: Base path of the repository

        Returns:
            List of parsed code structures with metadata
        """
        parsed_structures = []
        loop = asyncio.get_event_loop()

        # Process files concurrently
        async def parse_single_file(file_path: Path) -> Optional[Dict]:
            try:
                relative_path = file_path.relative_to(repo_path)

                # Read file content asynchronously
                async with aiofiles.open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    code_text = await f.read()

                # Run parser in executor (CPU-bound operation)
                parsed = await loop.run_in_executor(
                    None,
                    lambda: self.parser_service.parse_file(file_path, code_text, language=None)
                )

                return {
                    "file_path": str(relative_path),
                    "language": parsed.get("language", "unknown"),
                    "code_text": code_text,
                    "functions": parsed.get("functions", []),
                    "classes": parsed.get("classes", []),
                    "imports": parsed.get("imports", []),
                }

            except (OSError, ValueError, UnicodeDecodeError) as e:
                logger.warning("Failed to parse file %s: %s", file_path, e)
                return None

        # Parse files concurrently (limit concurrency to avoid overwhelming system)
        semaphore = asyncio.Semaphore(10)  # Process up to 10 files concurrently

        async def parse_with_semaphore(file_path: Path):
            async with semaphore:
                return await parse_single_file(file_path)

        tasks = [parse_with_semaphore(file_path) for file_path in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, dict):
                parsed_structures.append(result)
            elif isinstance(result, Exception):
                logger.warning("Exception parsing file: %s", result)

        logger.info("Parsed %d files", len(parsed_structures))
        return parsed_structures


    def _prepare_repository_data(
        self,
        owner: str,
        repo_name: str,
        repo_url: str,
        branch: Optional[str],
        commit_hash: Optional[str],
        files_processed: int,
        chunks_count: int,
        chunks_with_embeddings: int,
        parsing_stats: Optional[Dict]
    ) -> Dict:
        """
        Prepare repository data for MongoDB upsert.

        Args:
            owner: Repository owner
            repo_name: Repository name
            repo_url: Repository URL
            branch: Branch name
            commit_hash: Commit hash
            files_processed: Number of files processed
            chunks_count: Total chunks created
            chunks_with_embeddings: Chunks with embeddings
            parsing_stats: Parsing statistics

        Returns:
            Repository data dictionary
        """
        repo_data = {
            "owner": owner,
            "repo_name": repo_name,
            "repository_url": repo_url,
            "branch": branch,
            "commit_hash": commit_hash,
            "status": "completed",
            "files_processed": files_processed,
            "chunks_count": chunks_count,
            "chunks_with_embeddings": chunks_with_embeddings,
            "parsing_stats": ParsingStats(**parsing_stats).model_dump() if parsing_stats else None,
            "last_ingested_at": datetime.now(timezone.utc),
            "visibility": "public",
            "owner_user_id": None,
            "embedding_model": getattr(self.embedder, "model_name", settings.gemini_embedding_model),
            "embedding_dim": getattr(self.embedder, "dim", EMBEDDING_DIM),
            "schema_version": SCHEMA_VERSION,
        }
        return repo_data

    def _prepare_chunks_data(
        self,
        embedded_chunks: List[Dict],
        repo_object_id,
        repo_url: str,
        commit_hash: Optional[str],
    ) -> List[Dict]:
        chunks_data = []
        for chunk in embedded_chunks:
            metadata = chunk.get("metadata", {})
            chunk_metadata = ChunkMetadata(
                type=metadata.get("type", "file"),
                signature=metadata.get("signature"),
                is_split=metadata.get("is_split", False),
                split_index=metadata.get("split_index"),
                total_splits=metadata.get("total_splits")
            )
            chunks_data.append({
                "chunk_id": chunk["chunk_id"],
                "repo_id": repo_object_id if isinstance(repo_object_id, ObjectId) else ObjectId(repo_object_id),
                "repository_url": repo_url,
                "file_path": chunk["file_path"],
                "function_name": chunk.get("function_name"),
                "class_name": chunk.get("class_name"),
                "code_text": chunk["code_text"],
                "language": chunk["language"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "commit_hash": commit_hash,
                "content_hash": chunk.get("content_hash"),
                "point_id": chunk.get("point_id"),
                "embedded_at": chunk.get("embedded_at"),
                "embedding_model": chunk.get("embedding_model"),
                "schema_version": chunk.get("schema_version", SCHEMA_VERSION),
                "metadata": chunk_metadata.model_dump(),
            })
        return chunks_data

    async def _store_embeddings(
        self,
        chunks: List[Dict],
        owner: str,
        repo_name: str,
        repo_url: str,
        branch: Optional[str],
        commit_hash: Optional[str],
        files_processed: int,
        parsing_stats: Optional[Dict],
        request_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[ObjectId]]:
        """Reuse hashes, write Qdrant, then persist chunk text in Mongo."""
        repo_object_id = None
        try:
            seed = {
                "owner": owner,
                "repo_name": repo_name,
                "repository_url": repo_url,
                "branch": branch,
                "commit_hash": commit_hash,
                "status": "processing",
                "files_processed": files_processed,
                "chunks_count": len(chunks),
                "chunks_with_embeddings": 0,
                "visibility": "public",
                "owner_user_id": None,
            }
            existing = await self.mongodb_service.find_repository_by_identifier(
                owner, repo_name, branch
            )
            repo_object_id = existing["_id"] if existing else await self.mongodb_service.upsert_repository(seed)
            if not repo_object_id:
                repo_object_id = await self.mongodb_service.upsert_repository(seed)
            if not repo_object_id:
                logger.error("Failed to upsert repository in MongoDB")
                return False, None

            repo_id_str = str(repo_object_id)
            if request_id and self.job_queue:
                await self.job_queue.set_repo_id(request_id, repo_id_str)
            await self._ensure_active(request_id)

            hash_to_point = await self.mongodb_service.list_embedded_hash_index(repo_id_str)
            stale_ids = await self.mongodb_service.list_chunk_point_ids(repo_id_str)
            filled, embedded_count = await index_chunks(
                chunks,
                repo_id=repo_id_str,
                embedder=self.embedder,
                vector_store=self.vector_store,
                hash_to_point=hash_to_point,
                commit_hash=commit_hash or "",
                stale_point_ids=stale_ids,
                ensure_active=lambda: self._ensure_active(request_id),
            )

            await self._ensure_active(request_id)
            repo_data = self._prepare_repository_data(
                owner=owner,
                repo_name=repo_name,
                repo_url=repo_url,
                branch=branch,
                commit_hash=commit_hash,
                files_processed=files_processed,
                chunks_count=len(filled),
                chunks_with_embeddings=embedded_count,
                parsing_stats=parsing_stats
            )
            await self.mongodb_service.upsert_repository(repo_data)

            chunks_data = self._prepare_chunks_data(
                embedded_chunks=filled,
                repo_object_id=repo_object_id,
                repo_url=repo_url,
                commit_hash=commit_hash,
            )
            chunks_success = await self.mongodb_service.replace_repo_chunks(
                repo_id_str, chunks_data
            )
            if not chunks_success:
                logger.error("Failed to persist chunks in MongoDB for %s", repo_id_str)
                return False, None

            logger.info(
                "Stored %d chunks (%d embedded) for repository %s",
                len(filled), embedded_count, repo_id_str,
            )
            return True, repo_object_id if isinstance(repo_object_id, ObjectId) else ObjectId(repo_object_id)

        except JobCancelled:
            if repo_object_id:
                await self.mongodb_service.release_repo_if_processing(str(repo_object_id))
            raise
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("Failed to store embeddings: %s", e, exc_info=True)
            return False, None

    async def delete_repository(self, repo_id: str) -> Tuple[bool, str, Dict[str, bool]]:
        """
        Delete a repository and all its associated data.

        Flow:
        1. Delete all code chunks
        2. Delete all queries (if collection exists)
        3. Delete vector store files
        4. Delete repository record

        Args:
            repo_id: Repository identifier (MongoDB ObjectId as string)

        Returns:
            Tuple of (success: bool, message: str, details: Dict[str, bool])
        """
        details = {
            "chunks_deleted": False,
            "queries_deleted": False,
            "vector_store_deleted": False,
            "repository_deleted": False
        }

        try:
            # Get repository to extract owner and repo_name for vector_store_id
            repo = await self.mongodb_service.get_repository(repo_id)
            if not repo:
                error_msg = f"Repository not found: {repo_id}"
                logger.warning(error_msg)
                return False, error_msg, details

            logger.info("Starting deletion of repository: %s", repo_id)

            # Step 1: Delete all chunks
            logger.info("Deleting chunks for repository: %s", repo_id)
            details["chunks_deleted"] = await self.mongodb_service.delete_chunks_by_repo(repo_id)

            # Step 2: Delete all queries
            logger.info("Deleting queries for repository: %s", repo_id)
            details["queries_deleted"] = await self.mongodb_service.delete_queries_by_repo(repo_id)

            logger.info("Deleting vector points for repository: %s", repo_id)
            try:
                await self.vector_store.delete_by_repo(repo_id)
                details["vector_store_deleted"] = True
            except Exception as exc:
                logger.error("Failed to delete vector points for %s: %s", repo_id, exc, exc_info=True)
                details["vector_store_deleted"] = False

            # Step 4: Delete repository record (last step)
            logger.info("Deleting repository record: %s", repo_id)
            details["repository_deleted"] = await self.mongodb_service.delete_repository(repo_id)

            # Check overall success
            all_succeeded = all(details.values())

            if all_succeeded:
                success_msg = f"Successfully deleted repository {repo_id} and all associated data"
                logger.info(success_msg)
                return True, success_msg, details
            else:
                failed_steps = [k for k, v in details.items() if not v]
                error_msg = (
                    f"Partially deleted repository {repo_id}. "
                    f"Failed steps: {', '.join(failed_steps)}"
                )
                logger.warning(error_msg)
                return False, error_msg, details

        except Exception as e:
            error_msg = f"Failed to delete repository {repo_id}: {e}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg, details

    async def _cleanup_temp_repo(self, repo_path: Path) -> bool:
        """
        Clean up temporary repository directory.

        Args:
            repo_path: Path to temporary repository directory

        Returns:
            True if successful, False otherwise
        """
        try:
            if repo_path.exists():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, shutil.rmtree, repo_path)
                logger.info("Cleaned up temporary repository: %s", repo_path)
                return True
            return True
        except OSError as e:
            logger.error("Failed to cleanup temporary repository %s: %s", repo_path, e)
            return False

    async def ingest_repository(
        self,
        repo_url: str,
        branch: Optional[str] = None,
        cleanup: bool = True,
        request_id: Optional[str] = None,
        target_commit: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Orchestrate the complete ingestion pipeline for a repository.

        Pipeline: Clone → Parse → Chunk → Embed → Store → Cleanup

        Args:
            repo_url: URL of the repository to ingest
            branch: Specific branch to clone (optional)
            cleanup: Whether to cleanup temporary repository after processing
            request_id: Optional ingestion request ID for status tracking
            target_commit: Optional 7-char commit the job should check out

        Returns:
            Tuple of (success: bool, message: str)
        """
        repo_path = None
        parsing_stats: Optional[Dict] = None

        try:
            logger.info("Starting ingestion pipeline for repository: %s (branch: %s, request_id: %s)",
                repo_url, branch, request_id)
            await self._ensure_active(request_id)

            try:
                owner, repo_name = self._extract_repo_info(repo_url)
                branch = await resolve_github_default_branch(repo_url, branch)
                logger.info("Repository info - Owner: %s, Name: %s, branch: %s",
                    owner, repo_name, branch)
            except ValueError as e:
                error_msg = f"Failed to extract repository information: {e}"
                logger.error("%s", error_msg)
                return False, error_msg

            repo_path = self._prepare_temp_repo_path(repo_name)
            logger.info("Temporary repository path: %s", repo_path)

            await self._progress(
                request_id, stage="fetching", message="Cloning repository..."
            )
            loop = asyncio.get_event_loop()
            success, message, _repo = await loop.run_in_executor(
                None,
                lambda: RepositoryService.clone_repository(
                    repo_url=repo_url,
                    local_dir=str(repo_path),
                    branch=branch
                )
            )

            if not success:
                error_msg = f"Failed to clone repository: {message}"
                logger.error("%s", error_msg)
                return False, error_msg

            if target_commit:
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: self._checkout_commit_sync(repo_path, target_commit),
                    )
                except Exception as exc:
                    error_msg = f"Failed to check out commit {target_commit}: {exc}"
                    logger.error("%s", error_msg)
                    return False, error_msg

            logger.info("Repository cloned successfully: %s", message)
            await self._ensure_active(request_id)

            # Step 4: Get commit hash and update repo path if needed
            commit_hash = await self._get_commit_hash(repo_path)
            if commit_hash:
                logger.info("Repository commit hash: %s", commit_hash)

                # Rename temp repo path to use commit hash
                new_repo_path = self._prepare_temp_repo_path(repo_name, commit_hash)
                if repo_path != new_repo_path and new_repo_path.exists():
                    # If path with commit hash already exists, use it
                    if cleanup:
                        await self._cleanup_temp_repo(repo_path)
                    repo_path = new_repo_path
                elif repo_path != new_repo_path:
                    # Rename to new path with commit hash
                    try:
                        repo_path.rename(new_repo_path)
                        repo_path = new_repo_path
                        logger.info("Renamed temp repo path to: %s", repo_path)
                    except OSError as e:
                        logger.warning("Failed to rename temp repo path: %s", e)

            logger.info("Discovering code files...")
            code_files = await self._discover_code_files(repo_path)

            if not code_files:
                error_msg = "No code files found in repository"
                logger.warning("%s", error_msg)
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            logger.info("Found %d code files to process", len(code_files))
            await self._progress(
                request_id,
                stage="parsing",
                files_total=len(code_files),
                files_done=0,
                message="Parsing code files...",
            )
            parsed_structures = await self._parse_files(code_files, repo_path)

            if not parsed_structures:
                error_msg = "Failed to parse any files"
                logger.error(error_msg)
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            total_functions = sum(len(s.get("functions", [])) for s in parsed_structures)
            total_classes = sum(len(s.get("classes", [])) for s in parsed_structures)
            total_imports = sum(len(s.get("imports", [])) for s in parsed_structures)
            languages_found = list(set(s.get("language", "unknown") for s in parsed_structures))

            parsing_stats = {
                "files_parsed": len(parsed_structures),
                "total_functions": total_functions,
                "total_classes": total_classes,
                "total_imports": total_imports,
                "languages": languages_found
            }
            await self._progress(
                request_id,
                stage="chunking",
                files_done=len(parsed_structures),
                message="Chunking code...",
            )

            chunks = self.embedding_service.chunk_code(parsed_structures)

            if not chunks:
                error_msg = "Failed to chunk code"
                logger.error(error_msg)
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            logger.info("Created %d chunks", len(chunks))
            await self._progress(
                request_id,
                stage="embedding",
                chunks_total=len(chunks),
                chunks_embedded=0,
                message="Embedding chunks...",
            )

            logger.info("Indexing chunks in Qdrant and MongoDB...")
            try:
                store_success, repo_object_id = await self._store_embeddings(
                    chunks=chunks,
                    owner=owner,
                    repo_name=repo_name,
                    repo_url=repo_url,
                    branch=branch,
                    commit_hash=commit_hash,
                    files_processed=len(code_files),
                    parsing_stats=parsing_stats,
                    request_id=request_id,
                )
            except RuntimeError as e:
                if "quota exhausted" in str(e).lower():
                    error_msg = (
                        "Daily embedding quota exhausted. "
                        "Please wait 24 hours or upgrade to paid tier. "
                        f"Error: {e}"
                    )
                    logger.error(error_msg)
                    if cleanup:
                        await self._cleanup_temp_repo(repo_path)
                    return False, error_msg
                raise

            if not store_success:
                error_msg = "Failed to store embeddings"
                logger.error(error_msg)
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            await self._progress(
                request_id,
                stage="finalizing",
                chunks_embedded=len(chunks),
                message="Finalizing...",
            )

            if cleanup:
                logger.info("Cleaning up temporary repository...")
                await self._cleanup_temp_repo(repo_path)

            success_msg = (
                f"Successfully ingested repository {owner}/{repo_name} ({branch}). "
                f"Processed {len(code_files)} files and created {len(chunks)} chunks."
            )
            logger.info(success_msg)
            return True, success_msg

        except JobCancelled:
            if cleanup and repo_path:
                await self._cleanup_temp_repo(repo_path)
            raise
        except (OSError, ValueError, KeyError, RuntimeError, AttributeError) as e:
            error_msg = f"Unexpected error during ingestion: {e}"
            logger.error(error_msg, exc_info=True)
            if cleanup and repo_path:
                await self._cleanup_temp_repo(repo_path)
            return False, error_msg
