"""Pydantic schema for chat session documents in MongoDB."""

from datetime import datetime
from typing import Dict, List, Literal, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


class ChatMessageDocument(BaseModel):
    """Embedded chat message document."""

    message_id: str = Field(..., description="UUID for the message")
    role: Literal["user", "assistant"] = Field(..., description="Message author role")
    content: str = Field(..., description="Message content")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Message timestamp")
    chunks_used: Optional[List[str]] = Field(
        default=None,
        description="Chunk identifiers referenced when answering",
    )
    references: Optional[List[Dict]] = Field(
        default=None,
        description="File/snippet references returned to the client",
    )
    metadata: Optional[Dict] = Field(
        default=None,
        description="LLM metadata (model, token counts, etc.)",
    )
    status: Literal["completed", "streaming", "failed"] = Field(
        default="completed",
        description="Delivery status for the message",
    )


class ChatSessionSchema(BaseModel):
    """Schema for chat session documents."""

    session_id: str = Field(..., description="Public chat session identifier (unix timestamp)")
    user_id: Optional[ObjectId] = Field(default=None, description="User identifier")
    repo_id: ObjectId = Field(..., description="Repository identifier")
    title: Optional[str] = Field(default=None, description="Session title")
    summary: Optional[str] = Field(default=None, description="Rolling memory summary")
    last_summarized_count: int = Field(default=0,
        ge=0,
        le=100,
        description="Number of messages that have been summarized",
    )
    created_at: datetime = Field(default_factory=datetime.now(), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now(),
        description="Last update timestamp")
    messages: List[ChatMessageDocument] = Field(default_factory=list,
        description="Embedded chat messages")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "session_id": "1700000000000",
                "user_id": None,
                "repo_id": "507f1f77bcf86cd799439011",
                "title": "Initial Setup Questions",
                "summary": "Discussed Docker setup and .env configuration.",
                "created_at": "2024-01-15T10:00:00Z",
                "updated_at": "2024-01-15T10:15:00Z",
                "messages": [
                    {
                        "message_id": "f03a6b3a-3a10-4a8f-9bb3-123456789abc",
                        "role": "user",
                        "content": "How do I run the project locally?",
                        "timestamp": "2024-01-15T10:05:00Z",
                        "chunks_used": None,
                        "references": None,
                        "metadata": None,
                        "status": "completed",
                    }
                ],
            }
        },
    )
