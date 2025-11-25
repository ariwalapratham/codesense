"""Embedding service for chunking code and generating embeddings using Gemini."""

import asyncio
import logging
from typing import Dict, List, Optional, Sequence

import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None
    types = None
    GEMINI_AVAILABLE = False

from config.settings import settings

# Setup logger
logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for chunking code and generating embeddings using Gemini."""

    def __init__(self):
        """Initialize embedding service with configuration."""
        # Initialize Gemini API if available
        if GEMINI_AVAILABLE and settings.gemini_api_key:
            self.client = genai.Client(api_key=settings.gemini_api_key)
            self.embedding_model = settings.gemini_embedding_model
            self.gemini_available = True
            logger.info("Gemini embedding service initialized with model: %s", self.embedding_model)
        else:
            self.client = None
            self.embedding_model = None
            self.gemini_available = False
            if not GEMINI_AVAILABLE:
                logger.warning("google-genai not available. Embeddings will not be generated.")
            elif not settings.gemini_api_key:
                logger.warning("Gemini API key not configured. Embeddings will not be generated.")

        # Initialize text splitter for recursive chunking
        # Optimized for Gemini free tier: ~500-800 tokens per chunk
        # Approximate: 1 token ≈ 4 characters, so 2000-3200 chars per chunk
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2500,  # ~625 tokens (conservative for free tier)
            chunk_overlap=200,  # ~50 tokens overlap
            length_function=len,
            separators=[
                "\n\n\n",  # Triple newlines (section breaks)
                "\n\n",    # Double newlines (paragraph breaks)
                "\n",      # Single newlines
                " ",       # Spaces
                "",        # Characters (fallback)
            ],
        )

        # Token limit for semantic chunks (if exceeded, use recursive splitter)
        # ~800 tokens = ~3200 characters
        self.max_semantic_chunk_size = 3200

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text (approximate: 1 token ≈ 4 characters).

        Args:
            text: Text to estimate tokens for

        Returns:
            Estimated token count
        """
        return len(text) // 4

    def _extract_module_level_code(
        self,
        code_text: str,
        functions: List[Dict],
        classes: List[Dict]
    ) -> str:
        """
        Extract module-level code (code outside functions and classes).

        Args:
            code_text: Full file content
            functions: List of function dictionaries with start_line/end_line
            classes: List of class dictionaries with start_line/end_line

        Returns:
            Module-level code as string
        """
        lines = code_text.split("\n")

        # Create set of line ranges covered by functions and classes
        covered_lines = set()
        for func in functions:
            start = func.get("start_line", 1) - 1  # Convert to 0-based
            end = func.get("end_line", len(lines))
            covered_lines.update(range(start, end))

        for cls in classes:
            start = cls.get("start_line", 1) - 1
            end = cls.get("end_line", len(lines))
            covered_lines.update(range(start, end))

        # Extract lines not covered by functions/classes
        module_lines = [
            lines[i] for i in range(len(lines))
            if i not in covered_lines
        ]

        module_code = "\n".join(module_lines).strip()
        return module_code if module_code else ""

    def _create_chunk(
        self,
        chunk_id: str,
        file_path: str,
        code_text: str,
        start_line: int,
        end_line: int,
        language: str,
        function_name: Optional[str] = None,
        class_name: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Create a chunk dictionary with standard structure.

        Args:
            chunk_id: Unique chunk identifier
            file_path: File path relative to repository
            code_text: Code content
            start_line: Starting line number
            end_line: Ending line number
            language: Programming language
            function_name: Optional function name
            class_name: Optional class name
            metadata: Optional additional metadata

        Returns:
            Chunk dictionary
        """
        return {
            "chunk_id": chunk_id,
            "file_path": file_path,
            "function_name": function_name,
            "class_name": class_name,
            "code_text": code_text,
            "start_line": start_line,
            "end_line": end_line,
            "language": language,
            "metadata": metadata or {}
        }

    def chunk_code(self, parsed_structures: List[Dict]) -> List[Dict]:
        """
        Chunk parsed code into smaller pieces for embedding using hybrid strategy:
        1. Semantic chunks (functions/classes) if within token limit
        2. Recursive splitting for large functions/classes
        3. Module-level code extraction and recursive splitting

        Args:
            parsed_structures: List of parsed code structures

        Returns:
            List of code chunks with metadata
        """
        chunks = []
        chunk_counter = 0

        for structure in parsed_structures:
            file_path = structure["file_path"]
            code_text = structure["code_text"]
            language = structure["language"]
            functions = structure.get("functions", [])
            classes = structure.get("classes", [])
            imports = structure.get("imports", [])

            # Build imports context string
            imports_text = "\n".join([imp.get("text", "") for imp in imports])

            # 1. Chunk functions (with size check)
            for func in functions:
                func_name = func.get("name", "anonymous")
                func_body = func.get("body", "")
                func_signature = func.get("signature", "")
                func_start_line = func.get("start_line", 1)
                func_end_line = func.get("end_line", 1)

                # Add context: imports + signature + body
                func_with_context = (
                    f"{imports_text}\n\n{func_signature}\n{func_body}"
                    if imports_text
                    else f"{func_signature}\n{func_body}"
                )

                # Check if function exceeds token limit
                if len(func_with_context) > self.max_semantic_chunk_size:
                    # Split large function recursively
                    logger.debug(
                        "Function %s exceeds size limit (%d chars), splitting recursively",
                        func_name, len(func_with_context)
                    )
                    split_chunks = self.text_splitter.split_text(func_body)

                    for idx, split_text in enumerate(split_chunks):
                        chunk_id = f"{file_path}_func_{func_name}_{chunk_counter}_{idx}"
                        # Estimate line numbers for split chunks
                        lines_in_chunk = split_text.count("\n") + 1
                        estimated_start = func_start_line + (idx * lines_in_chunk)
                        estimated_end = min(estimated_start + lines_in_chunk, func_end_line)

                        chunks.append(self._create_chunk(
                            chunk_id=chunk_id,
                            file_path=file_path,
                            code_text=split_text,
                            start_line=estimated_start,
                            end_line=estimated_end,
                            language=language,
                            function_name=func_name,
                            metadata={
                                "signature": func_signature,
                                "type": "function",
                                "is_split": True,
                                "split_index": idx,
                                "total_splits": len(split_chunks)
                            }
                        ))
                        chunk_counter += 1
                else:
                    # Use function as single semantic chunk
                    chunk_id = f"{file_path}_func_{chunk_counter}"
                    chunks.append(self._create_chunk(
                        chunk_id=chunk_id,
                        file_path=file_path,
                        code_text=func_with_context,
                        start_line=func_start_line,
                        end_line=func_end_line,
                        language=language,
                        function_name=func_name,
                        metadata={
                            "signature": func_signature,
                            "type": "function"
                        }
                    ))
                    chunk_counter += 1

            # 2. Chunk classes (with size check)
            for cls in classes:
                cls_name = cls.get("name", "UnknownClass")
                cls_body = cls.get("body", "")
                cls_start_line = cls.get("start_line", 1)
                cls_end_line = cls.get("end_line", 1)

                # Add context: imports + class body
                cls_with_context = (
                    f"{imports_text}\n\nclass {cls_name}:\n{cls_body}"
                    if imports_text
                    else f"class {cls_name}:\n{cls_body}"
                )

                # Check if class exceeds token limit
                if len(cls_with_context) > self.max_semantic_chunk_size:
                    # Split large class recursively
                    logger.debug(
                        "Class %s exceeds size limit (%d chars), splitting recursively",
                        cls_name, len(cls_with_context)
                    )
                    split_chunks = self.text_splitter.split_text(cls_body)

                    for idx, split_text in enumerate(split_chunks):
                        chunk_id = f"{file_path}_class_{cls_name}_{chunk_counter}_{idx}"
                        lines_in_chunk = split_text.count("\n") + 1
                        estimated_start = cls_start_line + (idx * lines_in_chunk)
                        estimated_end = min(estimated_start + lines_in_chunk, cls_end_line)

                        chunks.append(self._create_chunk(
                            chunk_id=chunk_id,
                            file_path=file_path,
                            code_text=split_text,
                            start_line=estimated_start,
                            end_line=estimated_end,
                            language=language,
                            class_name=cls_name,
                            metadata={
                                "type": "class",
                                "is_split": True,
                                "split_index": idx,
                                "total_splits": len(split_chunks),
                                "imports": [imp.get("text", "") for imp in imports]
                            }
                        ))
                        chunk_counter += 1
                else:
                    # Use class as single semantic chunk
                    chunk_id = f"{file_path}_class_{chunk_counter}"
                    chunks.append(self._create_chunk(
                        chunk_id=chunk_id,
                        file_path=file_path,
                        code_text=cls_with_context,
                        start_line=cls_start_line,
                        end_line=cls_end_line,
                        language=language,
                        class_name=cls_name,
                        metadata={
                            "type": "class",
                            "imports": [imp.get("text", "") for imp in imports]
                        }
                    ))
                    chunk_counter += 1

            # 3. Extract and chunk module-level code
            module_code = self._extract_module_level_code(code_text, functions, classes)
            if module_code and module_code.strip():
                # Check if module code is substantial
                if len(module_code) > 100:  # Only chunk if meaningful
                    logger.debug(
                        "Found module-level code (%d chars) in %s, chunking recursively",
                        len(module_code), file_path
                    )
                    split_chunks = self.text_splitter.split_text(module_code)

                    for idx, split_text in enumerate(split_chunks):
                        chunk_id = f"{file_path}_module_{chunk_counter}_{idx}"
                        # Estimate line numbers for module code
                        lines_in_chunk = split_text.count("\n") + 1
                        estimated_start = 1 + (idx * lines_in_chunk)
                        estimated_end = estimated_start + lines_in_chunk

                        chunks.append(self._create_chunk(
                            chunk_id=chunk_id,
                            file_path=file_path,
                            code_text=split_text,
                            start_line=estimated_start,
                            end_line=estimated_end,
                            language=language,
                            metadata={
                                "type": "module_level",
                                "is_split": len(split_chunks) > 1,
                                "split_index": idx if len(split_chunks) > 1 else None,
                                "total_splits": len(split_chunks) if len(split_chunks) > 1 else None,
                                "imports": [imp.get("text", "") for imp in imports]
                            }
                        ))
                        chunk_counter += 1

            # 4. Fallback: if no functions/classes, chunk entire file recursively
            if not functions and not classes:
                logger.debug(
                    "No functions/classes found in %s, chunking entire file recursively",
                    file_path
                )
                split_chunks = self.text_splitter.split_text(code_text)

                for idx, split_text in enumerate(split_chunks):
                    chunk_id = f"{file_path}_file_{chunk_counter}_{idx}"
                    lines_in_chunk = split_text.count("\n") + 1
                    estimated_start = 1 + (idx * lines_in_chunk)
                    estimated_end = estimated_start + lines_in_chunk

                    chunks.append(self._create_chunk(
                        chunk_id=chunk_id,
                        file_path=file_path,
                        code_text=split_text,
                        start_line=estimated_start,
                        end_line=estimated_end,
                        language=language,
                        metadata={
                            "type": "file",
                            "is_split": len(split_chunks) > 1,
                            "split_index": idx if len(split_chunks) > 1 else None,
                            "total_splits": len(split_chunks) if len(split_chunks) > 1 else None
                        }
                    ))
                    chunk_counter += 1

        logger.info("Created %d code chunks using hybrid strategy", len(chunks))
        return chunks

    async def generate_embeddings(self, chunks: List[Dict], batch_size: int = 10) -> List[Dict]:
        """
        Generate embeddings for code chunks using Gemini.

        Args:
            chunks: List of code chunks
            batch_size: Number of chunks to process in each batch

        Returns:
            List of chunks with embeddings added
        """
        if not self.gemini_available:
            logger.warning("Gemini not available. Skipping embedding generation.")
            return chunks

        embedded_chunks = []
        total_chunks = len(chunks)

        logger.info("Generating embeddings for %d chunks (batch size: %d)", total_chunks, batch_size)

        # Process chunks in batches
        for i in range(0, total_chunks, batch_size):
            batch = chunks[i : i + batch_size]
            batch_texts = [chunk["code_text"] for chunk in batch]

            try:
                embedding_list = await self.generate_text_embeddings(batch_texts, allow_empty=True)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(
                    "Failed to generate embeddings for batch %d-%d: %s",
                    i + 1,
                    min(i + batch_size, total_chunks),
                    e,
                    exc_info=True,
                )
                embedding_list = [[] for _ in batch]

            if len(embedding_list) != len(batch):
                logger.warning(
                    "Mismatch in embedding count: expected %d, got %d",
                    len(batch),
                    len(embedding_list),
                )
                # pad/truncate
                if len(embedding_list) < len(batch):
                    embedding_list.extend([[] for _ in range(len(batch) - len(embedding_list))])
                else:
                    embedding_list = embedding_list[: len(batch)]

            for idx, chunk in enumerate(batch):
                embedding = embedding_list[idx]
                if embedding:
                    embedding_array = np.array(embedding, dtype=np.float32)
                    if np.any(np.isnan(embedding_array)) or np.any(np.isinf(embedding_array)):
                        logger.warning(
                            "Invalid embedding detected (NaN/Inf) for chunk %s, skipping",
                            chunk.get("chunk_id"),
                        )
                        chunk["embedding"] = None
                    else:
                        chunk["embedding"] = embedding
                else:
                    logger.warning(
                        "No embedding generated for chunk %s",
                        chunk.get("chunk_id"),
                    )
                    chunk["embedding"] = None
                embedded_chunks.append(chunk)

            logger.debug(
                "Generated embeddings for batch %d-%d/%d",
                i + 1,
                min(i + batch_size, total_chunks),
                total_chunks,
            )

        successful = sum(1 for chunk in embedded_chunks if chunk.get("embedding") is not None)
        logger.info(
            "Generated embeddings for %d/%d chunks successfully",
            successful, total_chunks
        )

        return embedded_chunks

    async def chunk_and_embed(self, parsed_structures: List[Dict], batch_size: int = 10) -> List[Dict]:
        """
        Convenience method to chunk code and generate embeddings in one call.

        Args:
            parsed_structures: List of parsed code structures
            batch_size: Number of chunks to process in each embedding batch

        Returns:
            List of chunks with embeddings added
        """
        # Step 1: Chunk code (run in executor as it's CPU-bound)
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(
            None,
            lambda: self.chunk_code(parsed_structures)
        )

        # Step 2: Generate embeddings
        embedded_chunks = await self.generate_embeddings(chunks, batch_size=batch_size)

        return embedded_chunks

    async def generate_text_embedding(self, text: str) -> List[float]:
        """Generate a single text embedding."""
        embeddings = await self.generate_text_embeddings([text])
        if not embeddings or not embeddings[0]:
            raise RuntimeError("Failed to generate embedding for text input.")
        return embeddings[0]

    async def generate_text_embeddings(
        self,
        texts: Sequence[str],
        allow_empty: bool = False,
        batch_size: int = 10,
    ) -> List[List[float]]:
        """
        Generate embeddings for a list of arbitrary texts.

        Args:
            texts: Sequence of strings to embed.
            allow_empty: If False, raise when service unavailable; if True return [].
            batch_size: Number of texts per Gemini request.
        """
        if not texts:
            return []
        if not self.gemini_available or not self.client or not self.embedding_model:
            if allow_empty:
                return [[] for _ in texts]
            raise RuntimeError("Gemini embedding service is not configured.")

        embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = list(texts[i : i + batch_size])
            batch_embeddings = await self._embed_text_batch(batch)
            embeddings.extend(batch_embeddings)
        return embeddings

    async def _embed_text_batch(self, texts: List[str]) -> List[List[float]]:
        """Internal helper that calls Gemini to embed a batch of texts."""
        loop = asyncio.get_running_loop()

        def _embed_sync(batch_texts: List[str]) -> List[List[float]]:
            kwargs = {"model": self.embedding_model, "contents": batch_texts}
            if types is not None:
                kwargs["config"] = types.EmbedContentConfig(output_dimensionality=768)
            response = self.client.models.embed_content(**kwargs)
            embedding_objs = getattr(response, "embeddings", []) or []
            results: List[List[float]] = []
            for idx in range(len(batch_texts)):
                if idx < len(embedding_objs) and hasattr(embedding_objs[idx], "values"):
                    results.append(list(embedding_objs[idx].values))
                else:
                    results.append([])
            return results

        return await loop.run_in_executor(None, _embed_sync, texts)
