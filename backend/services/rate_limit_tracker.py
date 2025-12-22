"""Rate limit tracker for Gemini API calls."""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class RateLimitTracker:
    """Track rate limits for Gemini API calls."""

    def __init__(
        self,
        rpm_limit: int = 100,  # Requests per minute
        tpm_limit: int = 30000,  # Tokens per minute
        rpd_limit: int = 1000,  # Requests per day
    ):
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.rpd_limit = rpd_limit

        # Track requests with timestamps
        self.request_times: deque = deque()  # (timestamp, token_count)
        self.daily_requests: deque = deque()  # timestamps

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        # Track daily reset time
        self._last_reset = datetime.now().date()

    async def can_make_request(self, estimated_tokens: int) -> Tuple[bool, Optional[float]]:
        """
        Check if a request can be made without exceeding limits.

        Args:
            estimated_tokens: Estimated token count for the request

        Returns:
            Tuple of (can_make_request, wait_time_seconds)
        """
        async with self._lock:
            now = time.time()
            now_date = datetime.now().date()

            # Reset daily counter if new day
            if now_date > self._last_reset:
                self.daily_requests.clear()
                self._last_reset = now_date

            # Clean old entries (older than 1 minute)
            cutoff_time = now - 60
            while self.request_times and self.request_times[0][0] < cutoff_time:
                self.request_times.popleft()

            # Clean old daily entries (older than 24 hours)
            cutoff_daily = now - 86400
            while self.daily_requests and self.daily_requests[0] < cutoff_daily:
                self.daily_requests.popleft()

            # Check daily limit
            if len(self.daily_requests) >= self.rpd_limit:
                # Calculate wait time until oldest request expires
                if self.daily_requests:
                    oldest_daily = self.daily_requests[0]
                    wait_time = (oldest_daily + 86400) - now
                    logger.warning(
                        "Daily quota exhausted: %d/%d requests. Wait %.1f seconds.",
                        len(self.daily_requests), self.rpd_limit, wait_time
                    )
                    return False, max(0, wait_time)
                return False, 86400  # Wait 24 hours if no requests tracked

            # Check RPM limit
            if len(self.request_times) >= self.rpm_limit:
                oldest_request = self.request_times[0][0]
                wait_time = (oldest_request + 60) - now
                logger.debug(
                    "RPM limit reached: %d/%d requests. Wait %.1f seconds.",
                    len(self.request_times), self.rpm_limit, wait_time
                )
                return False, max(0, wait_time)

            # Check TPM limit
            total_tokens_last_minute = sum(tokens for _, tokens in self.request_times)
            if total_tokens_last_minute + estimated_tokens > self.tpm_limit:
                # Calculate wait time until enough tokens are available
                if self.request_times:
                    oldest_request = self.request_times[0][0]
                    wait_time = (oldest_request + 60) - now
                else:
                    wait_time = 0
                logger.debug(
                    "TPM limit would be exceeded: %d + %d > %d. Wait %.1f seconds.",
                    total_tokens_last_minute, estimated_tokens, self.tpm_limit, wait_time
                )
                return False, max(0, wait_time)

            return True, None

    async def record_request(self, token_count: int):
        """Record a successful API request."""
        async with self._lock:
            now = time.time()
            self.request_times.append((now, token_count))
            self.daily_requests.append(now)
