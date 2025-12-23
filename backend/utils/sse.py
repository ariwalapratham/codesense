"""Server-Sent Events (SSE) utilities for FastAPI."""

import json
from typing import AsyncIterator, Dict, Any


async def format_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """
    Format a dictionary event as an SSE-formatted string.

    Args:
        event_type: SSE event type (e.g., 'chunk', 'done', 'error')
        data: Event data dictionary

    Returns:
        SSE-formatted string ready to send to client
    """
    json_data = json.dumps(data)
    return f"event: {event_type}\ndata: {json_data}\n\n"


async def sse_response_stream(
    event_stream: AsyncIterator[Dict[str, Any]]
) -> AsyncIterator[str]:
    """
    Convert a stream of dict events into SSE-formatted strings.

    Args:
        event_stream: Async iterator yielding dict events with 'type' field

    Yields:
        SSE-formatted strings (ready to send to client)
    """
    async for event in event_stream:
        event_type = event.get("type", "message")
        # Remove 'type' from data payload to avoid duplication
        data = {k: v for k, v in event.items() if k != "type"}
        yield await format_sse_event(event_type, data)
