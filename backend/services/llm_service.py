"""LLM service wrapper for generating assistant responses."""

from typing import Dict, List, Optional


class LLMService:
    """Wrapper around the Gemini (or other) LLM provider."""

    def __init__(self):
        # TODO: Initialize Gemini client and configuration (model name, temperature, etc.)
        self.model_name: Optional[str] = None

    async def generate_response(
        self,
        prompt: str,
        structured_context: Dict,
        stream: bool = False,
    ) -> Dict:
        """
        Generate an assistant response for the given prompt and context.

        Args:
            prompt: Final prompt string sent to the LLM.
            structured_context: Additional metadata (e.g., citations) needed
                to format the response payload.
            stream: Whether to stream the response back to the caller.

        Returns:
            Dictionary containing the assistant response payload.
        """
        # TODO:
        #   * Call the Gemini chat completion/streaming API here.
        #   * Handle streaming vs non-streaming responses.
        #   * Map provider-specific response into a normalized dict:
        #       {
        #           "content": "...",
        #           "references": [...],
        #           "raw": provider_response,
        #       }
        #   * Capture token usage / metadata for logging.
        raise NotImplementedError("LLMService.generate_response is not implemented yet.")
