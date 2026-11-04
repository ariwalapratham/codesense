"""Gemini embedder: 768-d output, L2-normalized, rate-limited."""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from config.settings import settings
from services.embeddings.base import EMBEDDING_DIM, check_vector, l2_normalize
from utils.rate_limit_tracker import RateLimitTracker

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

logger = logging.getLogger(__name__)


class GeminiEmbedder:
    dim = EMBEDDING_DIM

    def __init__(self) -> None:
        self.model_name = settings.gemini_embedding_model
        self.batch_size = 5
        self.batch_delay = 0.6
        self.max_retries = 5
        self.initial_backoff = 60.0
        self.max_backoff = 180.0
        self.rate_limit_tracker = RateLimitTracker(
            rpm_limit=100, tpm_limit=30000, rpd_limit=1000
        )
        if genai is not None and settings.gemini_api_key:
            self.client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=60_000),
            )
        else:
            self.client = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.client is None:
            raise RuntimeError("Gemini embedding service is not configured.")

        results: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = list(texts[i : i + self.batch_size])
            raw = await self._embed_batch(batch)
            for vector in raw:
                check_vector(vector)
                results.append(l2_normalize(vector))
            if i + self.batch_size < len(texts):
                await asyncio.sleep(self.batch_delay)
        return results

    async def _embed_batch(
        self, texts: list[str], retry_count: int = 0
    ) -> list[list[float]]:
        estimated_tokens = sum(len(text) // 4 for text in texts)
        _, wait_time = await self.rate_limit_tracker.can_make_request(estimated_tokens)
        if wait_time is not None:
            if wait_time > 3600:
                raise RuntimeError("Daily quota exhausted. Cannot generate embeddings.")
            await asyncio.sleep(wait_time)

        loop = asyncio.get_running_loop()

        def _call() -> list[list[float]]:
            kwargs: dict = {"model": self.model_name, "contents": texts}
            if types is not None:
                kwargs["config"] = types.EmbedContentConfig(
                    output_dimensionality=EMBEDDING_DIM
                )
            response = self.client.models.embed_content(**kwargs)
            objs = getattr(response, "embeddings", None) or []
            out: list[list[float]] = []
            for idx in range(len(texts)):
                if idx < len(objs) and getattr(objs[idx], "values", None):
                    out.append(list(objs[idx].values))
                else:
                    out.append([])
            return out

        try:
            result = await loop.run_in_executor(None, _call)
            await self.rate_limit_tracker.record_request(estimated_tokens)
            return result
        except Exception as exc:
            if not _is_rate_limit(exc) or retry_count >= self.max_retries:
                logger.error("Gemini embed failed: %s", exc, exc_info=True)
                raise
            _, retry_after = await self.rate_limit_tracker.can_make_request(
                estimated_tokens
            )
            backoff = max(retry_after or 0, self.initial_backoff * (2 ** retry_count))
            backoff = min(backoff, self.max_backoff)
            logger.warning(
                "Gemini rate limit (attempt %d/%d). Waiting %.1fs",
                retry_count + 1,
                self.max_retries,
                backoff,
            )
            await asyncio.sleep(backoff)
            return await self._embed_batch(texts, retry_count + 1)


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "429",
            "rate limit",
            "rate_limit",
            "quota exceeded",
            "resource exhausted",
            "too many requests",
        )
    )
