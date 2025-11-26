"""Chat service orchestrating the full Q&A pipeline."""

import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from services.llm_service import LLMService
from services.mongodb_service import MongoDBService
from services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)


class ChatServiceError(Exception):
    """Raised for controlled chat failures that should propagate to the API."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class ChatService:
    """Coordinates chat session validation, retrieval, and LLM responses."""

    def __init__(self):
        self.mongodb_service = MongoDBService()
        self.retrieval_service = RetrievalService()
        self.llm_service = LLMService()

        self.history_message_limit = 6
        self.retrieval_top_k = 15
        self.max_context_snippets = 6
        self.min_similarity_score = 0.15

    async def process_message(
        self,
        repo_id: str,
        session_id: str,
        user_message: str,
        stream: bool = False,
    ) -> Dict:
        """
        Process a user message and return the assistant response payload.
        """
        if stream:
            raise ChatServiceError(
                "Streaming responses are not implemented yet.", status_code=501
            )

        # 1. Get repository and chat session
        repo_doc = await self.mongodb_service.get_repository(repo_id)
        if not repo_doc:
            raise ChatServiceError("Repository not found.", status_code=404)

        session_doc = await self.mongodb_service.get_chat_session(repo_id, session_id)
        if not session_doc:
            raise ChatServiceError("Chat session not found.", status_code=404)

        # 2. Log user message and get its ID
        user_message_id = await self._log_message(
            repo_id=repo_id,
            session_id=session_id,
            role="user",
            content=user_message,
            status="completed",
        )
        logger.info(
            "Processing chat message (repo=%s, session=%s, user_message_id=%s)",
            repo_id,
            session_id,
            user_message_id,
        )

        # 3. Fetch recent chat messages and memory summary
        recent_messages = await self.mongodb_service.fetch_recent_chat_messages(
            repo_id=repo_id,
            session_id=session_id,
            limit=self.history_message_limit,
        )
        memory_summary = session_doc.get("summary")
        vector_store_id = self._build_vector_store_id(repo_doc)

        # 4. Generate query embedding and search repository chunks
        try:
            query_embedding = await self.retrieval_service.generate_query_embedding(
                user_message
            )
        except ValueError as exc:
            raise ChatServiceError(str(exc), status_code=400) from exc
        except RuntimeError as exc:
            raise ChatServiceError(str(exc), status_code=503) from exc

        # 5. Search repository chunks and filter by similarity score
        chunk_hits = await self.retrieval_service.search_repository_chunks(
            repo_vector_id=vector_store_id,
            embedding=query_embedding,
            top_k=self.retrieval_top_k,
        )
        filtered_hits = [
            (chunk_id, score)
            for chunk_id, score in chunk_hits
            if score >= self.min_similarity_score
        ]
        chunk_ids = [chunk_id for chunk_id, _ in filtered_hits]

        # 6. Fetch chunk metadata and build context string
        chunks = await self.retrieval_service.fetch_chunk_metadata(
            chunk_ids=chunk_ids,
            repo_id=repo_id,
        )
        context_string, references = self._build_context_string(chunks, filtered_hits)

        # 7. Generate LLM response (prompt building and LLM call handled in LLMService)
        try:
            llm_response = await self.llm_service.generate_chat_response(
                repo_doc=repo_doc,
                memory_summary=memory_summary,
                recent_messages=recent_messages,
                user_message=user_message,
                context_string=context_string,
                references=references,
                stream=stream,
            )
        except RuntimeError as exc:
            raise ChatServiceError(str(exc), status_code=503) from exc

        # 8. Log assistant response and return payload
        assistant_content = llm_response.get("content", "")
        response_metadata = llm_response.get("metadata", {})
        chunk_ids_used = [ref.get("chunk_id") for ref in references if ref.get("chunk_id")]

        assistant_message_id = await self._log_message(
            repo_id=repo_id,
            session_id=session_id,
            role="assistant",
            content=assistant_content,
            status="completed",
            references=references,
            chunks_used=chunk_ids_used,
            metadata=response_metadata,
        )

        return {
            "message_id": assistant_message_id,
            "role": "assistant",
            "content": assistant_content,
            "references": references,
            "metadata": response_metadata,
        }

    async def _log_message(
        self,
        repo_id: str,
        session_id: str,
        role: str,
        content: str,
        status: str = "completed",
        references: Optional[List[Dict]] = None,
        chunks_used: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """Persist a chat message and return its identifier."""
        message_id = str(uuid4())
        message_payload = {
            "message_id": message_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now(),
            "chunks_used": chunks_used,
            "references": references,
            "metadata": metadata,
            "status": status,
        }
        await self.mongodb_service.append_chat_message(
            repo_id=repo_id,
            session_id=session_id,
            message=message_payload,
        )
        return message_id

    def _build_context_string(
        self,
        chunks: Sequence[Dict],
        hits: Sequence[Tuple[str, float]],
    ) -> Tuple[str, List[Dict]]:
        """Create the context section fed to the LLM and reference metadata."""
        if not chunks:
            return "No relevant snippets were retrieved from the codebase.", []

        score_map = {chunk_id: score for chunk_id, score in hits}
        context_sections: List[str] = []
        references: List[Dict] = []

        for chunk in chunks[: self.max_context_snippets]:
            chunk_id = chunk.get("chunk_id")
            file_path = chunk.get("file_path")
            start_line = chunk.get("start_line")
            end_line = chunk.get("end_line")
            snippet = (chunk.get("code_text") or "").strip()
            score = score_map.get(chunk_id, 0.0)

            header = f"[ref {len(references) + 1}] {file_path}:{start_line}-{end_line}"
            context_sections.append(f"{header}\n{snippet}")

            references.append(
                {
                    "chunk_id": chunk_id,
                    "file_path": file_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "score": round(score, 4),
                }
            )

        context_text = "\n\n".join(context_sections)
        return context_text, references

    @staticmethod
    def _build_vector_store_id(repo_doc: Dict) -> str:
        """Convert owner/repo_name combo into the FAISS identifier."""
        owner = repo_doc.get("owner", "")
        repo_name = repo_doc.get("repo_name", "")
        vector_id = f"{owner}_{repo_name}".lower()
        vector_id = re.sub(r"[^a-z0-9_]+", "_", vector_id)
        vector_id = re.sub(r"_+", "_", vector_id).strip("_")
        return vector_id


