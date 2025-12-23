"""Ingestion service to orchestrate the complete pipeline: clone → parse → embed → store."""

import asyncio
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
from bson import ObjectId

import aiofiles
from git import Repo
from git.exc import InvalidGitRepositoryError

from config.settings import settings
from schemas import ChunkMetadata, ParsingStats
from services.embedding_service import EmbeddingService
from services.mongodb_service import MongoDBService
from services.parser_service import ParserService
from services.repository_service import RepositoryService
from services.vector_store_service import VectorStoreService

# Setup logger
logger = logging.getLogger(__name__)


class IngestionService:
    """Service to orchestrate repository ingestion pipeline."""

    def __init__(self):
        """Initialize ingestion service with configuration."""
        # Get temp_repos_path from settings
        self.temp_repos_path = Path(settings.temp_repos_path)
        self.temp_repos_path.mkdir(exist_ok=True, parents=True)

        # Initialize parser service
        self.parser_service = ParserService()

        # Initialize embedding service (handles chunking and embedding)
        self.embedding_service = EmbeddingService()

        # Initialize vector store service
        self.vector_store_service = VectorStoreService()

        # Initialize MongoDB service
        self.mongodb_service = MongoDBService()

    @staticmethod
    def _extract_repo_info(repo_url: str, branch: str) -> Tuple[str, str, str]:
        """
        Extract repository information from URL.

        Args:
            repo_url: Repository URL (GitHub/GitLab)

        Returns:
            Tuple of (owner, repo_name, vector_store_id)
        """
        parsed = urlparse(repo_url)
        path_parts = parsed.path.strip("/").split("/")

        if len(path_parts) >= 2:
            owner = path_parts[0]
            repo_name = path_parts[1].replace(".git", "")
        else:
            raise ValueError(f"Invalid repository URL format: {repo_url}")

        # Create sanitized vector_store_id: owner_repo_branch (for FAISS vector store)
        vector_store_id = f"{owner}_{repo_name}_{branch}".lower()
        vector_store_id = re.sub(r"[^a-z0-9_]", "_", vector_store_id)

        return owner, repo_name, vector_store_id

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
            code_files = await loop.run_in_executor(
                None,
                lambda: [
                    file_path
                    for file_path in repo_path.rglob("*")
                    if file_path.is_file()
                    and not any(skip_dir in file_path.parts for skip_dir in skip_dirs)
                    and file_path.suffix.lower() in supported_extensions
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
            "last_ingested_at": datetime.now()
        }
        return repo_data

    def _prepare_chunks_data(
        self,
        embedded_chunks: List[Dict],
        repo_object_id,
        repo_url: str,
        commit_hash: Optional[str],
        chunk_mapping: Dict[str, int]
    ) -> List[Dict]:
        """
        Prepare chunks data for MongoDB upsert.

        Args:
            embedded_chunks: List of chunks with embeddings
            repo_object_id: Repository ObjectId (ObjectId or str)
            repo_url: Repository URL
            commit_hash: Commit hash
            chunk_mapping: Mapping of chunk_id to embedding_index

        Returns:
            List of chunk data dictionaries
        """
        chunks_data = []
        for chunk in embedded_chunks:
            chunk_id = chunk["chunk_id"]
            embedding_index = chunk_mapping.get(chunk_id, -1)

            # Skip chunks without embedding index
            if embedding_index == -1:
                continue

            # Prepare chunk metadata
            metadata = chunk.get("metadata", {})
            chunk_metadata = ChunkMetadata(
                type=metadata.get("type", "file"),
                signature=metadata.get("signature"),
                is_split=metadata.get("is_split", False),
                split_index=metadata.get("split_index"),
                total_splits=metadata.get("total_splits")
            )

            chunk_data = {
                "chunk_id": chunk_id,
                "repo_id": repo_object_id if isinstance(repo_object_id, ObjectId) else ObjectId(repo_object_id),
                "repository_url": repo_url,
                "file_path": chunk["file_path"],
                "function_name": chunk.get("function_name"),
                "class_name": chunk.get("class_name"),
                "code_text": chunk["code_text"],
                "language": chunk["language"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "embedding_index": embedding_index,
                "commit_hash": commit_hash,
                "metadata": chunk_metadata.model_dump()
            }
            chunks_data.append(chunk_data)

        return chunks_data

    async def _store_embeddings(
        self,
        embedded_chunks: List[Dict],
        vector_store_id: str,
        owner: str,
        repo_name: str,
        repo_url: str,
        branch: Optional[str],
        commit_hash: Optional[str],
        files_processed: int,
        chunks_with_embeddings: int,
        parsing_stats: Optional[Dict]
    ) -> Tuple[bool, Optional[ObjectId]]:
        """
        Store embeddings in vector store and metadata in MongoDB.

        Args:
            embedded_chunks: List of chunks with embeddings
            vector_store_id: Vector store identifier (string, owner_repo format for FAISS)
            owner: Repository owner
            repo_name: Repository name
            repo_url: Original repository URL
            branch: Branch name
            commit_hash: Git commit hash
            files_processed: Number of files processed
            chunks_with_embeddings: Number of chunks with embeddings
            parsing_stats: Parsing statistics

        Returns:
            True if successful, False otherwise
        """

        try:
            # Store embeddings in FAISS (uses vector_store_id: owner_repo format)
            logger.info("Storing embeddings in FAISS for repository: %s", vector_store_id)
            faiss_success, chunk_mapping = await self.vector_store_service.save_embeddings(
                embedded_chunks=embedded_chunks,
                repo_id=vector_store_id,
                overwrite=True  # Always overwrite for single repo
            )

            if not faiss_success:
                logger.error("Failed to store embeddings in FAISS for repository: %s", vector_store_id)
                return False, None

            # Prepare repository data (without repo_id)
            repo_data = self._prepare_repository_data(
                owner=owner,
                repo_name=repo_name,
                repo_url=repo_url,
                branch=branch,
                commit_hash=commit_hash,
                files_processed=files_processed,
                chunks_count=len(embedded_chunks),
                chunks_with_embeddings=chunks_with_embeddings,
                parsing_stats=parsing_stats
            )

            # Upsert repository and get ObjectId
            logger.info("Upserting repository in MongoDB: %s/%s", owner, repo_name)
            repo_object_id = await self.mongodb_service.upsert_repository(repo_data)
            if not repo_object_id:
                logger.warning("Failed to upsert repository in MongoDB, continuing...")
                return False, None

            # Prepare chunks data with ObjectId
            chunks_data = self._prepare_chunks_data(
                embedded_chunks=embedded_chunks,
                repo_object_id=repo_object_id,
                repo_url=repo_url,
                commit_hash=commit_hash,
                chunk_mapping=chunk_mapping
            )

            # Delete old chunks for this repo (since we're overwriting)
            logger.info("Deleting old chunks for repository: %s", str(repo_object_id))
            await self.mongodb_service.delete_chunks_by_repo(str(repo_object_id))

            # Upsert chunks
            logger.info("Upserting %d chunks in MongoDB for repository: %s",
                len(chunks_data), str(repo_object_id))
            chunks_success = await self.mongodb_service.upsert_chunks(chunks_data)
            if not chunks_success:
                logger.warning("Failed to upsert some chunks in MongoDB, continuing...")

            logger.info("Stored %d chunks for repository %s", len(embedded_chunks), str(repo_object_id))
            return True, repo_object_id

        except (OSError, ValueError, KeyError) as e:
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

            owner = repo.get("owner")
            repo_name = repo.get("repo_name")
            branch = repo.get("branch")

            if not owner or not repo_name:
                error_msg = f"Repository missing owner or repo_name: {repo_id}"
                logger.error(error_msg)
                return False, error_msg, details

            # Generate vector_store_id (same logic as ingestion)
            vector_store_id = f"{owner}_{repo_name}_{branch}".lower()
            vector_store_id = re.sub(r"[^a-z0-9_]", "_", vector_store_id)

            logger.info("Starting deletion of repository: %s (vector_store_id: %s)",
                repo_id, vector_store_id)

            # Step 1: Delete all chunks
            logger.info("Deleting chunks for repository: %s", repo_id)
            details["chunks_deleted"] = await self.mongodb_service.delete_chunks_by_repo(repo_id)

            # Step 2: Delete all queries
            logger.info("Deleting queries for repository: %s", repo_id)
            details["queries_deleted"] = await self.mongodb_service.delete_queries_by_repo(repo_id)

            # Step 3: Delete vector store files
            logger.info("Deleting vector store for repository: %s", vector_store_id)
            # Vector store delete_index is synchronous, run in executor
            loop = asyncio.get_event_loop()
            details["vector_store_deleted"] = await loop.run_in_executor(
                None,
                self.vector_store_service.delete_index,
                vector_store_id
            )

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
        request_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Orchestrate the complete ingestion pipeline for a repository.

        Pipeline: Clone → Parse → Chunk → Embed → Store → Cleanup

        Args:
            repo_url: URL of the repository to ingest
            branch: Specific branch to clone (optional)
            cleanup: Whether to cleanup temporary repository after processing
            request_id: Optional ingestion request ID for status tracking

        Returns:
            Tuple of (success: bool, message: str)
        """
        repo_path = None
        parsing_stats: Optional[Dict] = None

        try:
            logger.info("Starting ingestion pipeline for repository: %s (branch: %s, request_id: %s)",
                repo_url, branch, request_id)

            # Update status to processing
            if request_id:
                await self.mongodb_service.update_ingestion_request_status(
                    request_id=request_id,
                    status="processing",
                    message="Processing repository..."
                )

            # Step 1: Extract repository information
            try:
                owner, repo_name, vector_store_id = self._extract_repo_info(repo_url, branch)
                logger.info("Repository info - Owner: %s, Name: %s, Vector Store ID: %s",
                    owner, repo_name, vector_store_id)
            except ValueError as e:
                error_msg = f"Failed to extract repository information: {e}"
                logger.error("%s", error_msg)
                if request_id:
                    await self.mongodb_service.update_ingestion_request_status(
                        request_id=request_id,
                        status="failed",
                        error=error_msg
                    )
                return False, error_msg

            # Step 2: Prepare temporary repository path (initially without commit hash)
            repo_path = self._prepare_temp_repo_path(repo_name)
            logger.info("Temporary repository path: %s", repo_path)

            # Step 3: Clone repository (run in executor as it's blocking)
            logger.info("Cloning repository...")
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
                if request_id:
                    await self.mongodb_service.update_ingestion_request_status(
                        request_id=request_id,
                        status="failed",
                        error=error_msg
                    )
                return False, error_msg

            logger.info("Repository cloned successfully: %s", message)

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

            # Step 5: Discover code files
            logger.info("Discovering code files...")
            code_files = await self._discover_code_files(repo_path)

            if not code_files:
                error_msg = "No code files found in repository"
                logger.warning("%s", error_msg)
                if request_id:
                    await self.mongodb_service.update_ingestion_request_status(
                        request_id=request_id,
                        status="failed",
                        error=error_msg
                    )
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            logger.info("Found %d code files to process", len(code_files))

            # Step 6: Parse files
            logger.info("Parsing code files...")
            parsed_structures = await self._parse_files(code_files, repo_path)

            if not parsed_structures:
                error_msg = "Failed to parse any files"
                logger.error(error_msg)
                if request_id:
                    await self.mongodb_service.update_ingestion_request_status(
                        request_id=request_id,
                        status="failed",
                        error=error_msg
                    )
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            # Calculate parsing statistics
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

            logger.info(
                "Parsing complete: %d files, %d functions, %d classes, %d imports",
                len(parsed_structures), total_functions, total_classes, total_imports
            )

            # Step 7: Chunk code and generate embeddings
            logger.info("Chunking code and generating embeddings...")
            try:
                embedded_chunks = await self.embedding_service.chunk_and_embed(
                    parsed_structures,
                    batch_size=None  # Use service's default (rate-limited) batch size
                )
            except RuntimeError as e:  # pylint: disable=broad-exception-caught
                if "quota exhausted" in str(e).lower():
                    error_msg = (
                        "Daily embedding quota exhausted. "
                        "Please wait 24 hours or upgrade to paid tier. "
                        f"Error: {e}"
                    )
                    logger.error(error_msg)
                    if request_id:
                        await self.mongodb_service.update_ingestion_request_status(
                            request_id=request_id,
                            status="failed",
                            error=error_msg
                        )
                    if cleanup:
                        await self._cleanup_temp_repo(repo_path)
                    return False, error_msg
                raise

            if not embedded_chunks:
                error_msg = "Failed to chunk code and generate embeddings"
                logger.error(error_msg)
                if request_id:
                    await self.mongodb_service.update_ingestion_request_status(
                        request_id=request_id,
                        status="failed",
                        error=error_msg
                    )
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            # Count chunks with successful embeddings
            chunks_with_embeddings = sum(
                1 for chunk in embedded_chunks
                if chunk.get("embedding") is not None
            )

            logger.info(
                "Created %d chunks, %d with embeddings",
                len(embedded_chunks), chunks_with_embeddings
            )

            # Step 9: Store embeddings and metadata
            logger.info("Storing embeddings and metadata...")
            store_success, repo_object_id = await self._store_embeddings(
                embedded_chunks=embedded_chunks,
                vector_store_id=vector_store_id,
                owner=owner,
                repo_name=repo_name,
                repo_url=repo_url,
                branch=branch,
                commit_hash=commit_hash,
                files_processed=len(code_files),
                chunks_with_embeddings=chunks_with_embeddings,
                parsing_stats=parsing_stats
            )

            if not store_success:
                error_msg = "Failed to store embeddings"
                logger.error(error_msg)
                if request_id:
                    await self.mongodb_service.update_ingestion_request_status(
                        request_id=request_id,
                        status="failed",
                        error=error_msg
                    )
                if cleanup:
                    await self._cleanup_temp_repo(repo_path)
                return False, error_msg

            # Link repository ID to ingestion request
            if request_id and repo_object_id:
                await self.mongodb_service.update_ingestion_request_status(
                    request_id=request_id,
                    status="processing",
                    repo_id=str(repo_object_id)
                )

            # Step 10: Cleanup temporary repository
            if cleanup:
                logger.info("Cleaning up temporary repository...")
                await self._cleanup_temp_repo(repo_path)

            success_msg = (
                f"Successfully ingested repository {vector_store_id}. "
                f"Processed {len(code_files)} files and created {len(embedded_chunks)} chunks."
            )
            logger.info(success_msg)

            # Update status to completed
            if request_id:
                await self.mongodb_service.update_ingestion_request_status(
                    request_id=request_id,
                    status="completed",
                    message=success_msg
                )

            return True, success_msg

        # Top-level orchestration method needs broad exception handling
        # to ensure cleanup and proper error reporting
        except (OSError, ValueError, KeyError, RuntimeError, AttributeError) as e:
            error_msg = f"Unexpected error during ingestion: {e}"
            logger.error(error_msg, exc_info=True)

            # Update status to failed
            if request_id:
                await self.mongodb_service.update_ingestion_request_status(
                    request_id=request_id,
                    status="failed",
                    error=error_msg
                )

            # Cleanup on error
            if cleanup and repo_path:
                await self._cleanup_temp_repo(repo_path)

            return False, error_msg
