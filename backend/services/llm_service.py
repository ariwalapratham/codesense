"""LLM service wrapper for generating assistant responses."""

import asyncio
import logging
from typing import Dict, List, Optional, Sequence

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None
    GEMINI_AVAILABLE = False

from config.settings import settings
from utils.prompts import SYSTEM_INSTRUCTIONS, build_title_prompt
logger = logging.getLogger(__name__)


class LLMService:
    """Wrapper around the Gemini (or other) LLM provider."""

    def __init__(self):
        """Initialize LLM service with Gemini client if available."""
        if GEMINI_AVAILABLE and settings.gemini_api_key:
            self.client = genai.Client(api_key=settings.gemini_api_key)
            self.model_name = settings.gemini_llm_model
            self.fallback_model = settings.gemini_llm_fallback_model
            self.gemini_available = True
            logger.info(
                "Gemini LLM service initialized with model: %s (fallback: %s)",
                self.model_name,
                self.fallback_model,
            )
        else:
            self.client = None
            self.model_name = None
            self.fallback_model = None
            self.gemini_available = False
            if not GEMINI_AVAILABLE:
                logger.warning("google-genai not available. LLM responses will not be generated.")
            elif not settings.gemini_api_key:
                logger.warning("Gemini API key not available. LLM responses will not be generated.")

    async def generate_chat_response(
        self,
        repo_doc: Dict,
        memory_summary: Optional[str],
        recent_messages: Sequence[Dict],
        user_message: str,
        context_string: str,
        references: List[Dict],
        stream: bool = False,
    ) -> Dict:
        """
        Generate an assistant response for a chat message.

        This method builds the prompt from the provided context and calls the LLM.

        Args:
            repo_doc: Repository document from MongoDB.
            memory_summary: Optional long-term memory summary for the session.
            recent_messages: Recent chat messages for context.
            user_message: Current user message.
            context_string: Formatted context string with retrieved code snippets.
            references: List of reference metadata (file paths, chunk IDs, etc.).
            stream: Whether to stream the response (not implemented yet).

        Returns:
            Dictionary containing:
                - content: Assistant response text
                - references: List of reference metadata
                - metadata: LLM metadata (model, token counts, etc.)
        """
        if stream:
            raise NotImplementedError("Streaming responses are not implemented yet.")

        # Build the final prompt
        prompt = self._build_prompt(
            repo_doc=repo_doc,
            memory_summary=memory_summary,
            recent_messages=recent_messages,
            user_message=user_message,
            context=context_string,
        )

        # Call the LLM
        if not self.gemini_available:
            raise RuntimeError("Gemini LLM service is not available.")

        try:
            response = await self._call_gemini(prompt, model=self.model_name)
            return {
                "content": response.get("content", ""),
                "references": references,
                "metadata": response.get("metadata", {}),
            }
        except (RuntimeError, Exception) as e:
            # Check if this is a rate limit or quota error that can be retried with fallback
            if self._is_retryable_error(e) and self.fallback_model:
                logger.warning(
                    "Primary model %s failed with error: %s. Retrying with fallback model: %s",
                    self.model_name,
                    str(e),
                    self.fallback_model,
                )
                try:
                    response = await self._call_gemini(prompt, model=self.fallback_model)
                    return {
                        "content": response.get("content", ""),
                        "references": references,
                        "metadata": response.get("metadata", {}),
                    }
                except Exception as fallback_error:
                    logger.error(
                        "Fallback model %s also failed: %s",
                        self.fallback_model,
                        fallback_error,
                        exc_info=True,
                    )
                    raise RuntimeError(
                        f"Both primary and fallback models failed. Last error:{str(fallback_error)}"
                    ) from fallback_error
            else:
                logger.error("Failed to generate LLM response: %s", e, exc_info=True)
                raise RuntimeError(f"Failed to generate LLM response: {str(e)}") from e

    def _build_prompt(
        self,
        repo_doc: Dict,
        memory_summary: Optional[str],
        recent_messages: Sequence[Dict],
        user_message: str,
        context: str,
    ) -> str:
        """Assemble the final prompt sent to the LLM."""

        memory_section = (
            f"Long-term summary: {memory_summary}"
            if memory_summary
            else "Long-term summary: (not available)."
        )
        history_section = self._format_history(recent_messages)
        repo_label = f"{repo_doc.get('owner', 'unknown')}/{repo_doc.get('repo_name', 'unknown')}"

        prompt = (
            f"{SYSTEM_INSTRUCTIONS}\n\n"
            f"Repository: {repo_label}\n"
            f"{memory_section}\n\n"
            f"Recent conversation:\n{history_section}\n\n"
            f"Retrieved context:\n{context}\n\n"
            f"Current user question:\n{user_message.strip()}\n\n"
            "Compose a helpful response below:\n"
        )
        return prompt

    def _format_history(self, recent_messages: Sequence[Dict]) -> str:
        """Format prior chat turns for the prompt."""
        if not recent_messages:
            return "(no prior messages in this session)"

        lines = []
        for message in recent_messages:
            role = message.get("role", "user").capitalize()
            content = (message.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _is_retryable_error(self, error: Exception) -> bool:
        """
        Check if an error is retryable with a fallback model.

        Retryable errors include:
        - Rate limit errors (429)
        - Quota exceeded errors
        - Service unavailable errors (503)
        - Resource exhausted errors
        """
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()

        # Check for rate limit indicators
        retryable_indicators = [
            "rate limit",
            "rate_limit",
            "429",
            "quota exceeded",
            "quota_exceeded",
            "resource exhausted",
            "resource_exhausted",
            "service unavailable",
            "service_unavailable",
            "503",
            "too many requests",
            "throttled",
        ]

        return any(
            indicator in error_str or indicator in error_type
            for indicator in retryable_indicators
        )

    async def _call_gemini(self, prompt: str, model: Optional[str] = None) -> Dict:
        """
        Call the Gemini API to generate a response using async/await pattern.

        According to the Gemini API documentation:
        https://ai.google.dev/gemini-api/docs/text-generation

        The generate_content method is synchronous, so we use run_in_executor
        to make it non-blocking in an async context.

        Args:
            prompt: The prompt string to send to the LLM.
            model: Model name to use (defaults to self.model_name).

        Returns:
            Dictionary containing:
                - content: Generated response text
                - metadata: Token usage and model information
        """
        if model is None:
            model = self.model_name

        # Use get_running_loop() instead of get_event_loop() for better async compatibility
        loop = asyncio.get_running_loop()

        def _generate_sync():
            """
            Synchronous wrapper for Gemini API call.
            The google.genai library's generate_content is synchronous,
            so we run it in an executor to avoid blocking the event loop.
            """
            try:
                # Call Gemini API as per documentation:
                # https://ai.google.dev/gemini-api/docs/text-generation
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                )

                # Extract response text
                content = response.text if hasattr(response, "text") else ""

                # Extract metadata if available
                metadata = {
                    "model": model,
                    "provider": "gemini",
                }

                # Try to extract token usage if available
                if hasattr(response, "usage_metadata"):
                    usage = response.usage_metadata
                    metadata["tokens"] = {
                        "prompt": getattr(usage, "prompt_token_count", 0),
                        "completion": getattr(usage, "completion_token_count", 0),
                        "total": getattr(usage, "total_token_count", 0),
                    }

                return {
                    "content": content,
                    "metadata": metadata,
                }
            except Exception as e:
                # Re-raise with context for error handling
                logger.error(
                    "Gemini API call failed for model %s: %s",
                    model,
                    e,
                    exc_info=True,
                )
                raise RuntimeError(f"Gemini API error: {str(e)}") from e

        # Run the synchronous call in an executor to make it async-friendly
        return await loop.run_in_executor(None, _generate_sync)

    async def generate_summary(self, prompt: str) -> str:
        """
        Generate a conversation summary from a prompt.

        Args:
            prompt: The summary generation prompt.

        Returns:
            Generated summary text, or empty string on failure.
        """
        if not self.gemini_available:
            logger.warning("Gemini not available, cannot generate summary")
            return ""

        try:
            response = await self._call_gemini(prompt, model=self.model_name)
            summary = response.get("content", "").strip()
            return summary
        except Exception as e:
            logger.error("Failed to generate summary: %s", e, exc_info=True)
            return ""

    async def generate_title(self, first_message: str) -> str:
        """
        Generate a short title from the first user message.

        Args:
            first_message: The first user message in the conversation.

        Returns:
            Generated title, or empty string on failure.
        """
        if not self.gemini_available:
            logger.warning("Gemini not available, cannot generate title")
            return ""

        prompt = build_title_prompt(first_message)

        try:
            response = await self._call_gemini(prompt, model=self.model_name)
            title = response.get("content", "").strip()

            # Clean up title (remove quotes, limit length)
            title = title.strip('"\'')
            if len(title) > 50:
                title = title[:47] + "..."

            return title
        except Exception as e:
            logger.error("Failed to generate title: %s", e, exc_info=True)
            return ""
