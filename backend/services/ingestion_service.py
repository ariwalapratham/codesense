"""Ingestion service to orchestrate the complete pipeline: clone → parse → embed → store."""

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from config.settings import settings
from services.embedding_service import EmbeddingService
from services.parser_service import ParserService
from services.repository_service import RepositoryService

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
        
        # TODO: Initialize vector_store_service when implemented
        # from services.vector_store_service import VectorStoreService
        # self.vector_store_service = VectorStoreService()
        
        # TODO: Initialize MongoDB connection when implemented
        # from database.mongodb import get_database
        # self.db = get_database()

    @staticmethod
    def _extract_repo_info(repo_url: str) -> Tuple[str, str, str]:
        """
        Extract repository information from URL.

        Args:
            repo_url: Repository URL (GitHub/GitLab)

        Returns:
            Tuple of (owner, repo_name, repo_id)
        """
        parsed = urlparse(repo_url)
        path_parts = parsed.path.strip("/").split("/")
        
        if len(path_parts) >= 2:
            owner = path_parts[0]
            repo_name = path_parts[1].replace(".git", "")
        else:
            raise ValueError(f"Invalid repository URL format: {repo_url}")
        
        # Create sanitized repo_id: owner_repo
        repo_id = f"{owner}_{repo_name}".lower()
        repo_id = re.sub(r"[^a-z0-9_]", "_", repo_id)
        
        return owner, repo_name, repo_id

    def _prepare_temp_repo_path(self, repo_id: str) -> Path:
        """
        Prepare temporary directory path for repository clone.

        Args:
            repo_id: Sanitized repository identifier

        Returns:
            Path to temporary repository directory
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        temp_dir = self.temp_repos_path / f"{repo_id}_{timestamp}"
        return temp_dir

    def _get_commit_hash(self, repo_path: Path) -> Optional[str]:
        """
        Get the current commit hash of the repository.

        Args:
            repo_path: Path to the cloned repository

        Returns:
            Commit hash string or None
        """
        try:
            from git import Repo
            repo = Repo(repo_path)
            return repo.head.commit.hexsha[:7]
        except Exception as e:
            logger.warning("Failed to get commit hash: %s", e)
            return None

    def _discover_code_files(self, repo_path: Path) -> List[Path]:
        """
        Discover all code files in the repository.

        Args:
            repo_path: Path to the cloned repository

        Returns:
            List of file paths to process
        """
        # TODO: Implement file discovery logic
        # - Walk through repository directory
        # - Filter by file extensions (.py, .js, .ts, .java, .go, etc.)
        # - Skip directories: .git, node_modules, __pycache__, venv, etc.
        # - Respect .gitignore if present
        # - Return list of Path objects
        
        code_files = []
        supported_extensions = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", 
            ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".php", 
            ".swift", ".kt", ".rs", ".scala", ".r", ".m", ".ipynb"
        }
        
        skip_dirs = {".git", "node_modules", "__pycache__", "venv", 
                     ".venv", "env", ".env", "dist", "build", ".pytest_cache"}
        
        try:
            for file_path in repo_path.rglob("*"):
                if file_path.is_file():
                    # Skip files in ignored directories
                    if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
                        continue
                    
                    # Check if file has supported extension
                    if file_path.suffix.lower() in supported_extensions:
                        code_files.append(file_path)
            
            logger.info("Discovered %d code files in repository", len(code_files))
            return code_files
            
        except Exception as e:
            logger.error("Error discovering code files: %s", e)
            return []

    def _parse_files(self, files: List[Path], repo_path: Path) -> List[Dict]:
        """
        Parse code files to extract structures (functions, classes, etc.).

        Args:
            files: List of file paths to parse
            repo_path: Base path of the repository

        Returns:
            List of parsed code structures with metadata
        """
        # TODO: Implement parsing logic using parser_service
        # - Use tree-sitter to parse each file
        # - Extract functions, classes, methods, imports
        # - Return list of dictionaries with:
        #   {
        #       "file_path": str (relative to repo),
        #       "language": str,
        #       "functions": [...],
        #       "classes": [...],
        #       "imports": [...],
        #       "code_text": str (full file content)
        #   }
        
        parsed_structures = []
        
        for file_path in files:
            try:
                relative_path = file_path.relative_to(repo_path)
                
                # Read file content
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    code_text = f.read()
                
                # Use parser_service to parse AST (language will be auto-detected from file extension)
                parsed = self.parser_service.parse_file(file_path, code_text, language=None)
                
                parsed_structures.append({
                    "file_path": str(relative_path),
                    "language": parsed.get("language", "unknown"),
                    "code_text": code_text,
                    "functions": parsed.get("functions", []),
                    "classes": parsed.get("classes", []),
                    "imports": parsed.get("imports", []),
                })
                
            except Exception as e:
                logger.warning("Failed to parse file %s: %s", file_path, e)
                continue
        
        logger.info("Parsed %d files", len(parsed_structures))
        return parsed_structures


    def _store_embeddings(self, embedded_chunks: List[Dict], repo_id: str, 
                         repo_url: str, commit_hash: Optional[str]) -> bool:  # noqa: ARG002
        """
        Store embeddings in vector store and metadata in MongoDB.

        Args:
            embedded_chunks: List of chunks with embeddings
            repo_id: Repository identifier
            repo_url: Original repository URL
            commit_hash: Git commit hash

        Returns:
            True if successful, False otherwise
        """
        # TODO: Implement storage logic
        # 1. Store embeddings in FAISS vector store
        #    - Create/load FAISS index for this repository
        #    - Add embeddings to index
        #    - Save index to {vector_store_path}/{repo_id}.faiss
        #
        # 2. Store metadata in MongoDB
        #    - Insert documents into 'code_chunks' collection
        #    - Each document should contain:
        #      {
        #          "chunk_id": str,
        #          "repo_id": str,
        #          "repository_url": str,
        #          "file_path": str,
        #          "function_name": Optional[str],
        #          "class_name": Optional[str],
        #          "code_text": str,
        #          "language": str,
        #          "start_line": int,
        #          "end_line": int,
        #          "embedding_index": int (index in FAISS),
        #          "commit_hash": Optional[str],
        #          "created_at": datetime
        #      }
        
        try:
            # TODO: Store in FAISS
            # vector_store_path = Path(settings.vector_store_path)
            # vector_store_path.mkdir(exist_ok=True)
            # index_path = vector_store_path / f"{repo_id}.faiss"
            # self.vector_store_service.save_embeddings(embedded_chunks, index_path)
            
            # TODO: Store metadata in MongoDB
            # collection = self.db["code_chunks"]
            # documents = []
            # for idx, chunk in enumerate(embedded_chunks):
            #     doc = {
            #         "chunk_id": chunk["chunk_id"],
            #         "repo_id": repo_id,
            #         "repository_url": repo_url,
            #         "file_path": chunk["file_path"],
            #         "function_name": chunk.get("function_name"),
            #         "class_name": chunk.get("class_name"),
            #         "code_text": chunk["code_text"],
            #         "language": chunk["language"],
            #         "start_line": chunk["start_line"],
            #         "end_line": chunk["end_line"],
            #         "embedding_index": idx,
            #         "commit_hash": commit_hash,
            #         "created_at": datetime.utcnow()
            #     }
            #     documents.append(doc)
            # collection.insert_many(documents)
            
            logger.info("Stored %d chunks for repository %s", len(embedded_chunks), repo_id)
            return True
            
        except Exception as e:
            logger.error("Failed to store embeddings: %s", e)
            return False

    def _cleanup_temp_repo(self, repo_path: Path) -> bool:
        """
        Clean up temporary repository directory.

        Args:
            repo_path: Path to temporary repository directory

        Returns:
            True if successful, False otherwise
        """
        try:
            if repo_path.exists():
                shutil.rmtree(repo_path)
                logger.info("Cleaned up temporary repository: %s", repo_path)
                return True
            return True
        except Exception as e:
            logger.error("Failed to cleanup temporary repository %s: %s", repo_path, e)
            return False

    def ingest_repository(
        self,
        repo_url: str,
        branch: Optional[str] = None,
        cleanup: bool = True
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Orchestrate the complete ingestion pipeline for a repository.

        Pipeline: Clone → Parse → Chunk → Embed → Store → Cleanup

        Args:
            repo_url: URL of the repository to ingest
            branch: Specific branch to clone (optional)
            cleanup: Whether to cleanup temporary repository after processing

        Returns:
            Tuple of (success: bool, message: str, metadata: Optional[Dict])
        """
        repo_path = None
        metadata = {
            "repo_url": repo_url,
            "status": "failed",
            "chunks_processed": 0,
            "files_processed": 0
        }
        
        try:
            logger.info("Starting ingestion pipeline for repository: %s", repo_url)
            
            # Step 1: Extract repository information
            try:
                owner, repo_name, repo_id = self._extract_repo_info(repo_url)
                logger.info("Repository info - Owner: %s, Name: %s, ID: %s", owner, repo_name, repo_id)
            except Exception as e:
                error_msg = f"Failed to extract repository information: {e}"
                logger.error("%s", error_msg)
                return False, error_msg, None
            
            # Step 2: Prepare temporary repository path
            repo_path = self._prepare_temp_repo_path(repo_id)
            logger.info("Temporary repository path: %s", repo_path)
            
            # Step 3: Clone repository
            logger.info("Cloning repository...")
            success, message, _repo = RepositoryService.clone_repository(
                repo_url=repo_url,
                local_dir=str(repo_path),
                branch=branch
            )
            
            if not success:
                error_msg = f"Failed to clone repository: {message}"
                logger.error("%s", error_msg)
                return False, error_msg, None
            
            logger.info("Repository cloned successfully: %s", message)
            
            # Step 4: Get commit hash
            commit_hash = self._get_commit_hash(repo_path)
            if commit_hash:
                logger.info("Repository commit hash: %s", commit_hash)
                metadata["commit_hash"] = commit_hash
            
            # Step 5: Discover code files
            logger.info("Discovering code files...")
            code_files = self._discover_code_files(repo_path)
            
            if not code_files:
                error_msg = "No code files found in repository"
                logger.warning("%s", error_msg)
                if cleanup:
                    self._cleanup_temp_repo(repo_path)
                return False, error_msg, None
            
            metadata["files_processed"] = len(code_files)
            logger.info("Found %d code files to process", len(code_files))
            
            # Step 6: Parse files
            logger.info("Parsing code files...")
            parsed_structures = self._parse_files(code_files, repo_path)
            
            if not parsed_structures:
                error_msg = "Failed to parse any files"
                logger.error(error_msg)
                if cleanup:
                    self._cleanup_temp_repo(repo_path)
                return False, error_msg, None
            
            # Calculate parsing statistics
            total_functions = sum(len(s.get("functions", [])) for s in parsed_structures)
            total_classes = sum(len(s.get("classes", [])) for s in parsed_structures)
            total_imports = sum(len(s.get("imports", [])) for s in parsed_structures)
            languages_found = list(set(s.get("language", "unknown") for s in parsed_structures))
            
            metadata["parsing_stats"] = {
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
            embedded_chunks = self.embedding_service.chunk_and_embed(
                parsed_structures,
                batch_size=10  # Process 10 chunks at a time for embeddings
            )
            
            if not embedded_chunks:
                error_msg = "Failed to chunk code and generate embeddings"
                logger.error(error_msg)
                if cleanup:
                    self._cleanup_temp_repo(repo_path)
                return False, error_msg, None
            
            # Count chunks with successful embeddings
            chunks_with_embeddings = sum(
                1 for chunk in embedded_chunks
                if chunk.get("embedding") is not None
            )
            metadata["chunks_processed"] = len(embedded_chunks)
            metadata["chunks_with_embeddings"] = chunks_with_embeddings
            
            logger.info(
                "Created %d chunks, %d with embeddings",
                len(embedded_chunks), chunks_with_embeddings
            )
            
            # Step 9: Store embeddings and metadata
            logger.info("Storing embeddings and metadata...")
            store_success = self._store_embeddings(
                embedded_chunks=embedded_chunks,
                repo_id=repo_id,
                repo_url=repo_url,
                commit_hash=commit_hash
            )
            
            if not store_success:
                error_msg = "Failed to store embeddings"
                logger.error(error_msg)
                if cleanup:
                    self._cleanup_temp_repo(repo_path)
                return False, error_msg, None
            
            # Step 10: Cleanup temporary repository
            if cleanup:
                logger.info("Cleaning up temporary repository...")
                self._cleanup_temp_repo(repo_path)
            
            # Success
            metadata.update({
                "status": "completed",
                "repo_id": repo_id,
                "owner": owner,
                "repo_name": repo_name,
                "repo_path": str(repo_path) if repo_path else None
            })
            
            success_msg = (
                f"Successfully ingested repository {repo_id}. "
                f"Processed {len(code_files)} files and created {len(embedded_chunks)} chunks."
            )
            logger.info(success_msg)
            
            return True, success_msg, metadata
            
        except Exception as e:
            error_msg = f"Unexpected error during ingestion: {e}"
            logger.error(error_msg, exc_info=True)
            
            # Cleanup on error
            if cleanup and repo_path:
                self._cleanup_temp_repo(repo_path)
            
            return False, error_msg, metadata
