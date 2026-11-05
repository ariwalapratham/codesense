"""Chat service orchestrating the full Q&A pipeline."""

import logging
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from core.errors import AppError, code_for_status
from services.embeddings.base import Embedder
from services.llm_service import LLMService
from services.mongodb_service import MongoDBService
from services.retrieval_service import RetrievalService
from services.vector_store.base import VectorStore
from utils.prompts import build_summary_prompt

#pylint: disable=broad-exception-caught

logger = logging.getLogger(__name__)


class ChatServiceError(AppError):
    """Controlled chat failure mapped to the API error envelope."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        details: Optional[Dict] = None,
    ):
        super().__init__(
            message,
            code=code_for_status(status_code),
            status_code=status_code,
            details=details,
        )


class ChatService:
    """Coordinates chat session validation, retrieval, and LLM responses."""

    def __init__(self, *, embedder: Embedder, vector_store: VectorStore):
        self.mongodb_service = MongoDBService()
        self.retrieval_service = RetrievalService(embedder=embedder, vector_store=vector_store)
        self.llm_service = LLMService()

        self.history_message_limit = 6
        self.retrieval_top_k = 15
        self.max_context_snippets = 6
        self.min_similarity_score = 0.15
        self.summary_batch_size = 10  # Summarize when we have 10+ old messages

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

        # 4. Generate query embedding and search repository chunks
        try:
            query_embedding = await self.retrieval_service.generate_query_embedding(
                user_message
            )
            chunk_hits = await self.retrieval_service.search_repository_chunks(
                repo_id=repo_id,
                embedding=query_embedding,
                top_k=self.retrieval_top_k,
            )
        except ValueError as exc:
            raise ChatServiceError(str(exc), status_code=400) from exc
        except RuntimeError as exc:
            logger.error("Retrieval upstream failed: %s", exc)
            raise ChatServiceError(
                "Upstream provider unavailable", status_code=503
            ) from exc

        # 5. Filter repository chunks by similarity score
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
            logger.error("LLM provider failed: %s", exc)
            raise ChatServiceError(
                "Upstream provider unavailable", status_code=503
            ) from exc

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
            "content": assistant_content,
            "references": references,
            "metadata": response_metadata,
        }

    async def process_message_stream(
        self,
        repo_id: str,
        session_id: str,
        user_message: str,
    ) -> AsyncIterator[Dict]:
        """
        Process a user message and stream the assistant response token-by-token.

        Yields:
            Dict with structure:
            {
                "type": "chunk" | "done" | "error",
                "content": str,  # For chunks
                "message_id": str,  # For done event
                "references": List[Dict],  # For done event
                "metadata": Dict,  # For done event
                "error": str,  # For error event
            }
        """
        try:
            # 1. Get repository and chat session
            repo_doc = await self.mongodb_service.get_repository(repo_id)
            if not repo_doc:
                yield {
                    "type": "error",
                    "error": "Repository not found.",
                }
                return

            session_doc = await self.mongodb_service.get_chat_session(repo_id, session_id)
            if not session_doc:
                yield {
                    "type": "error",
                    "error": "Chat session not found.",
                }
                return

            # 2. Log user message immediately
            user_message_id = await self._log_message(
                repo_id=repo_id,
                session_id=session_id,
                role="user",
                content=user_message,
                status="completed",
            )
            logger.info(
                "Processing streaming chat message (repo=%s, session=%s, user_message_id=%s)",
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

            # 4. Generate query embedding and search repository chunks
            try:
                query_embedding = await self.retrieval_service.generate_query_embedding(
                    user_message
                )
                chunk_hits = await self.retrieval_service.search_repository_chunks(
                    repo_id=repo_id,
                    embedding=query_embedding,
                    top_k=self.retrieval_top_k,
                )
            except ValueError as exc:
                yield {
                    "type": "error",
                    "error": str(exc),
                }
                return
            except RuntimeError as exc:
                yield {
                    "type": "error",
                    "error": str(exc),
                }
                return

            # 5. Filter repository chunks by similarity score
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

            # 7. Stream LLM response
            accumulated_content = ""
            response_metadata = {}
            try:
                async for chunk in self.llm_service.generate_chat_response_stream(
                    repo_doc=repo_doc,
                    memory_summary=memory_summary,
                    recent_messages=recent_messages,
                    user_message=user_message,
                    context_string=context_string,
                    references=references,
                ):
                    accumulated_content += chunk
                    yield {
                        "type": "chunk",
                        "content": chunk,
                    }
            except RuntimeError as exc:
                yield {
                    "type": "error",
                    "error": str(exc),
                }
                return

            # 8. Log complete assistant message
            chunk_ids_used = [ref.get("chunk_id") for ref in references if ref.get("chunk_id")]
            assistant_message_id = await self._log_message(
                repo_id=repo_id,
                session_id=session_id,
                role="assistant",
                content=accumulated_content,
                status="completed",
                references=references,
                chunks_used=chunk_ids_used,
                metadata=response_metadata,
            )

            # 9. Send final metadata event
            yield {
                "type": "done",
                "message_id": assistant_message_id,
                "references": references,
                "metadata": response_metadata,
            }

        except Exception as e:
            logger.error(
                "Error in process_message_stream (repo=%s session=%s): %s",
                repo_id,
                session_id,
                e,
                exc_info=True,
            )
            yield {
                "type": "error",
                "error": f"Failed to process message: {str(e)}",
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

    def should_generate_summary(self, total_messages: int) -> bool:
        """
        Determine if summary generation should be triggered.

        Returns True if:
        - Total messages > history_limit AND
        - We have accumulated enough old messages (batch_size) since last summary
        """
        if total_messages <= self.history_message_limit:
            return False  # Not enough messages yet

        old_message_count = total_messages - self.history_message_limit
        # Only summarize when we have accumulated enough messages
        return old_message_count >= self.summary_batch_size

    async def generate_summary_background(
        self,
        repo_id: str,
        session_id: str,
    ) -> None:
        """
        Background task to generate/update conversation summary.

        This runs asynchronously after the response is sent to the user.
        It takes existing summary + older messages and generates a new summary.
        """
        try:
            # Get all messages
            all_messages, total = await self.mongodb_service.get_chat_messages(
                repo_id=repo_id,
                session_id=session_id,
                skip=0,
                limit=1000,
            )

            if not self.should_generate_summary(total):
                return  # Not enough messages to summarize yet

            # Get existing summary and last_summarized_count
            session_doc = await self.mongodb_service.get_chat_session(repo_id, session_id)
            existing_summary = session_doc.get("summary")
            last_summarized_count = session_doc.get("last_summarized_count", 0)

            # Messages beyond recent limit
            old_messages = all_messages[: -(self.history_message_limit + 1)]

            if not old_messages:
                return

            # Get messages that haven't been summarized yet
            new_messages_to_summarize = old_messages[last_summarized_count:]

            if not new_messages_to_summarize:
                return  # All messages already summarized

            # Build summary prompt
            summary_prompt = self._build_summary_prompt(existing_summary, new_messages_to_summarize)

            # Generate summary
            new_summary = await self.llm_service.generate_summary(summary_prompt)

            if new_summary:
                # Update both summary and last_summarized_count
                await self.mongodb_service.update_chat_session(
                    repo_id=repo_id,
                    session_id=session_id,
                    updates={
                        "summary": new_summary,
                        "last_summarized_count": len(old_messages),
                    },
                )
                logger.info(
                    "Generated summary for session %s (repo=%s, %d old messages)",
                    session_id,
                    repo_id,
                    len(old_messages),
                )
        except Exception as e:
            logger.error(
                "Failed to generate summary in background (repo=%s, session=%s): %s",
                repo_id,
                session_id,
                e,
                exc_info=True,
            )
            # Don't raise - this is a background task

    def _build_summary_prompt(
        self, existing_summary: Optional[str], messages: List[Dict]
    ) -> str:
        """Build prompt for summary generation."""
        messages_text = "\n".join(
            [
                f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}"
                for msg in messages
            ]
        )
        return build_summary_prompt(existing_summary, messages_text)

    async def generate_title_background(
        self,
        repo_id: str,
        session_id: str,
        first_user_message: str,
    ) -> None:
        """
        Background task to generate a title for a new chat session.

        Uses the first user message to generate a concise title.
        """
        try:
            # Check if title is still default
            session_doc = await self.mongodb_service.get_chat_session(repo_id, session_id)
            current_title = session_doc.get("title")

            # Only update if it's still "New Chat" or None
            if current_title and current_title.lower() not in ["new chat", "new", ""]:
                return  # Title already set

            # Generate title from first message
            title = await self.llm_service.generate_title(first_user_message)

            if title:
                await self.mongodb_service.update_chat_session(
                    repo_id=repo_id,
                    session_id=session_id,
                    updates={"title": title},
                )
                logger.info("Generated title '%s' for session %s (repo=%s)",
                                title, session_id, repo_id)
        except Exception as e:
            logger.error(
                "Failed to generate title in background (repo=%s, session=%s): %s",
                repo_id,
                session_id,
                e,
                exc_info=True,
            )
            # Don't raise - this is a background task
