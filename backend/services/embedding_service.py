"""Chunk parsed code. Embeddings come from the injected Embedder."""

import logging
from typing import Dict, List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Hybrid code chunker. Does not own a Gemini client."""

    def __init__(self):
        """Configure the recursive splitter used when a symbol exceeds the size cap."""
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2500,
            chunk_overlap=200,
            length_function=len,
            separators=[
                "\n\n\n",
                "\n\n",
                "\n",
                " ",
                "",
            ],
        )
        self.max_semantic_chunk_size = 3200

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
