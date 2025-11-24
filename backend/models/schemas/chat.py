"""Pydantic schemas for chat sessions and messages."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

class ChatSessionCreateRequest(BaseModel):
    """Payload for creating a chat session."""

    title: Optional[str] = Field(None, description="Session title")


class ChatSessionUpdateRequest(BaseModel):
    """Payload for updating chat session metadata."""

    title: Optional[str] = Field(None, description="Session title")


class ChatSessionResponse(BaseModel):
    """Detailed chat session representation."""

    session_id: str = Field(..., description="Chat session identifier")
    repo_id: str = Field(..., description="Repository identifier")
    title: Optional[str] = Field(None, description="Session title")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ChatSessionSummary(BaseModel):
    """Summary info for chat session list (sidebar cards)."""

    session_id: str = Field(..., description="Chat session identifier (UUID)")
    repo_id: str = Field(..., description="Associated repository identifier")
    title: Optional[str] = Field(None, description="User-friendly session title")
    last_message_preview: Optional[str] = Field(None, description="Preview of the most recent message")
    created_at: datetime = Field(..., description="Session creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "3b1a7e6e-2c6f-4b17-8f1c-123456789abc",
                "repo_id": "507f1f77bcf86cd799439011",
                "title": "Initial Setup Questions",
                "last_message_preview": "Let's start by checking the Docker setup...",
                "created_at": "2024-01-15T10:00:00Z",
                "updated_at": "2024-01-15T10:15:00Z",
            }
        }
    )


class ChatMessageRequest(BaseModel):
    """Incoming user message payload."""

    message: str = Field(..., description="Raw user message text")
    stream: bool = Field(False, description="Whether to stream the assistant response")
    metadata: Optional[Dict] = Field(None, description="Optional client metadata")


class ChatMessageResponse(BaseModel):
    """Assistant response payload."""

    message_id: str = Field(..., description="Identifier for the stored assistant message")
    content: str = Field(..., description="Assistant response text")
    references: Optional[List[Dict]] = Field(
        None,
        description="List of snippet/file references cited in the response",
    )
    metadata: Optional[Dict] = Field(
        None,
        description="LLM metadata (token usage, model name, latency, etc.)",
    )


class ChatMessageItem(BaseModel):
    """Individual chat message in a conversation."""

    message_id: str = Field(..., description="Unique message identifier")
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")
    timestamp: datetime = Field(..., description="Message timestamp")
    chunks_used: Optional[List[str]] = Field(None, description="List of chunk_ids used for context")
    references: Optional[List[Dict]] = Field(None, description="List of references (file_path, chunk_id)")
    metadata: Optional[Dict] = Field(None, description="Additional message metadata")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "123e4567-e89b-12d3-a456-426614174000",
                "role": "user",
                "content": "How do I use the `upsert_repository` function?",
                "timestamp": "2024-07-20T12:00:00Z",
                "chunks_used": ["chunk_abc123"],
                "references": [{"file_path": "services/mongodb_service.py", "chunk_id": "chunk_abc123"}],
                "metadata": {"model": "gemini-pro", "tokens": 150},
            }
        }
    )


class ChatMessageListResponse(BaseModel):
    """Response model for listing chat messages."""

    messages: List[ChatMessageItem] = Field(..., description="List of chat messages")
    total: int = Field(..., description="Total number of messages in the session")
    skip: int = Field(..., description="Number of messages skipped")
    limit: int = Field(..., description="Maximum number of messages returned")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "messages": [],
                "total": 10,
                "skip": 0,
                "limit": 50,
            }
        }
    )


class ChatSessionListResponse(BaseModel):
    """Response model for listing chat sessions."""

    sessions: List[ChatSessionSummary] = Field(..., description="List of chat sessions")
    total: int = Field(..., description="Total number of chat sessions")
    skip: int = Field(..., description="Number of sessions skipped")
    limit: int = Field(..., description="Maximum number of sessions returned")


