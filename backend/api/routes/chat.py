"""Chat API routes."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from api.deps import get_chat_service, get_mongodb_service
from core.errors import AppError
from models.schemas import (
    ChatMessageItem,
    ChatMessageListResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatSessionCreateRequest,
    ChatSessionListResponse,
    ChatSessionResponse,
    ChatSessionSummary,
    ChatSessionUpdateRequest,
)
from services.chat_service import ChatService
from services.mongodb_service import MongoDBService
from utils.sse import format_sse_event

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/repository/{repo_id}/chat-sessions",
    tags=["chat"],
)


@router.get(
    "/",
    response_model=ChatSessionListResponse,
    summary="List chat sessions",
    description="List chat sessions for a repository with pagination.",
)
async def list_chat_sessions(
    repo_id: str,
    skip: int = Query(0, ge=0, description="Number of sessions to skip"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of sessions to return"),
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
) -> ChatSessionListResponse:
    """Return paginated chat sessions for a repository."""
    try:
        sessions, total = await mongodb_service.list_chat_sessions(repo_id, skip=skip, limit=limit)
        summaries = []
        for session in sessions:
            last_message = None
            messages = session.get("messages") or []
            if messages:
                last_message = messages[-1].get("content")
            summaries.append(
                ChatSessionSummary(
                    session_id=session["session_id"],
                    repo_id=session["repo_id"],
                    title=session.get("title"),
                    last_message_preview=last_message,
                    created_at=session.get("created_at"),
                    updated_at=session.get("updated_at"),
                )
            )
        return ChatSessionListResponse(
            sessions=summaries,
            total=total,
            skip=skip,
            limit=limit,
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to list chat sessions for repo %s: %s", repo_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list chat sessions: {exc}",
        ) from exc


@router.post(
    "/",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create chat session",
    description="Create a new chat session for a repository.",
)
async def create_chat_session(
    repo_id: str,
    payload: ChatSessionCreateRequest,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
) -> ChatSessionResponse:
    """Create a chat session."""
    try:
        session = await mongodb_service.create_chat_session(
            repo_id=repo_id,
            title=payload.title,
        )
        if not session:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create chat session",
            )

        return ChatSessionResponse(
            session_id=session["session_id"],
            repo_id=session["repo_id"],
            title=session.get("title"),
            created_at=session.get("created_at"),
            updated_at=session.get("updated_at"),
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to create chat session for repo %s: %s", repo_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create chat session: {exc}",
        ) from exc


@router.patch(
    "/{session_id}",
    response_model=ChatSessionResponse,
    summary="Update chat session",
    description="Rename or update metadata for a chat session.",
)
async def update_chat_session(
    repo_id: str,
    session_id: str,
    payload: ChatSessionUpdateRequest,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
) -> ChatSessionResponse:
    """Update chat session metadata."""
    try:
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field must be provided",
            )

        updated = await mongodb_service.update_chat_session(repo_id, session_id, updates)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat session not found",
            )

        session_doc = await mongodb_service.get_chat_session(repo_id, session_id)
        if not session_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat session not found after update",
            )

        return ChatSessionResponse(
            session_id=session_doc["session_id"],
            repo_id=session_doc["repo_id"],
            title=session_doc.get("title"),
            created_at=session_doc.get("created_at"),
            updated_at=session_doc.get("updated_at"),
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to update chat session %s for repo %s: %s",
            session_id,
            repo_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update chat session: {exc}",
        ) from exc


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete chat session",
    description="Delete a chat session and its messages.",
)
async def delete_chat_session(
    repo_id: str,
    session_id: str,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
):
    """Delete a chat session."""
    try:
        deleted = await mongodb_service.delete_chat_session(repo_id, session_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat session not found",
            )
        return None

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to delete chat session %s for repo %s: %s",
            session_id,
            repo_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete chat session: {exc}",
        ) from exc


@router.get(
    "/{session_id}/messages",
    response_model=ChatMessageListResponse,
    summary="List messages in a chat session",
    description="Get paginated list of messages for a chat session.",
)
async def list_messages(
    repo_id: str,
    session_id: str,
    skip: int = Query(0, ge=0, description="Number of messages to skip"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of messages to return"),
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
) -> ChatMessageListResponse:
    """Get paginated messages for a chat session."""
    try:
        messages, total = await mongodb_service.get_chat_messages(
            repo_id=repo_id,
            session_id=session_id,
            skip=skip,
            limit=limit,
        )

        message_items = [
            ChatMessageItem(
                message_id=msg.get("message_id", ""),
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                timestamp=msg.get("timestamp"),
                chunks_used=msg.get("chunks_used"),
                references=msg.get("references"),
                metadata=msg.get("metadata"),
            )
            for msg in messages
        ]

        return ChatMessageListResponse(
            messages=message_items,
            total=total,
            skip=skip,
            limit=limit,
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to list messages for session %s in repo %s: %s",
            session_id,
            repo_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list messages: {exc}",
        ) from exc


@router.post(
    "/{session_id}/messages",
    response_model=ChatMessageResponse,
    summary="Send message to chat session",
    description="Process a user query and return the assistant response.",
)
async def send_message(
    repo_id: str,
    session_id: str,
    payload: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
    chat_service: ChatService = Depends(get_chat_service),
) -> ChatMessageResponse:
    """
    Entry point for chat messages.

    Args:
        repo_id: Repository identifier (Mongo ObjectId as string).
        session_id: Chat session identifier (UUID/string).
        payload: Request body containing user message (and optional flags).
        background_tasks: FastAPI background tasks for async operations.

    Returns:
        Assistant response payload (non-streaming placeholder for now).
    """
    try:
        user_message = payload.message

        # Get session to check message count for background tasks (before processing)
        session_doc = await mongodb_service.get_chat_session(repo_id, session_id)
        if not session_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat session not found",
            )

        messages_before = session_doc.get("messages", [])
        is_first_message = len(messages_before) == 0  # No messages yet

        # TODO(qa): Support streaming (SSE/WebSockets). For now, call the chat service directly.
        response = await chat_service.process_message(
            repo_id=repo_id,
            session_id=session_id,
            user_message=user_message,
            stream=payload.stream,
        )

        # Add background tasks after response is sent
        if is_first_message:
            # Generate title from first message
            background_tasks.add_task(
                chat_service.generate_title_background,
                repo_id=repo_id,
                session_id=session_id,
                first_user_message=user_message,
            )

        # Check if summary generation is needed
        # After processing, we'll have 2 more messages (user + assistant)
        total_after = len(messages_before) + 2
        if chat_service.should_generate_summary(total_after):
            background_tasks.add_task(
                chat_service.generate_summary_background,
                repo_id=repo_id,
                session_id=session_id,
            )

        return ChatMessageResponse(**response)

    except HTTPException:
        raise
    except AppError:
        raise
    except NotImplementedError as exc:
        logger.warning("ChatService not implemented: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to process chat message (repo=%s session=%s): %s",
            repo_id,
            session_id,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process chat message: {exc}",
        ) from exc


@router.post(
    "/{session_id}/messages/stream",
    summary="Send message with streaming response (SSE)",
    description=(
        "Process a user query and stream the assistant response via "
        "Server-Sent Events (SSE)."
    ),
)
async def send_message_stream(
    repo_id: str,
    session_id: str,
    payload: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
    chat_service: ChatService = Depends(get_chat_service),
):
    """
    Stream chat response using Server-Sent Events (SSE).

    This endpoint streams the assistant's response token-by-token as it's generated,
    providing real-time feedback to the client.

    Args:
        repo_id: Repository identifier (Mongo ObjectId as string).
        session_id: Chat session identifier (UUID/string).
        payload: Request body containing user message.
        background_tasks: FastAPI background tasks for async operations.

    Returns:
        StreamingResponse with SSE-formatted events:
        - event: "chunk" - Contains partial response text
        - event: "done" - Contains final message_id, references, and metadata
        - event: "error" - Contains error message if something goes wrong
    """
    try:
        user_message = payload.message

        # Get session to check message count for background tasks
        session_doc = await mongodb_service.get_chat_session(repo_id, session_id)
        if not session_doc:
            # Send error via SSE
            async def error_stream():
                yield await format_sse_event(
                    "error",
                    {"error": "Chat session not found"},
                )

            return StreamingResponse(
                error_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # Disable nginx buffering
                },
            )

        messages_before = session_doc.get("messages", [])
        is_first_message = len(messages_before) == 0

        async def generate_sse_stream():
            """Generate SSE events from chat service stream."""
            accumulated_content = ""
            try:
                async for event in chat_service.process_message_stream(
                    repo_id=repo_id,
                    session_id=session_id,
                    user_message=user_message,
                ):
                    event_type = event.get("type")

                    if event_type == "chunk":
                        accumulated_content += event.get("content", "")
                        yield await format_sse_event("chunk", {"content": event.get("content", "")})

                    elif event_type == "done":
                        # Trigger background tasks after streaming completes
                        if is_first_message:
                            background_tasks.add_task(
                                chat_service.generate_title_background,
                                repo_id=repo_id,
                                session_id=session_id,
                                first_user_message=user_message,
                            )

                        total_after = len(messages_before) + 2
                        if chat_service.should_generate_summary(total_after):
                            background_tasks.add_task(
                                chat_service.generate_summary_background,
                                repo_id=repo_id,
                                session_id=session_id,
                            )

                        # Send final event with metadata
                        yield await format_sse_event(
                            "done",
                            {
                                "message_id": event.get("message_id"),
                                "references": event.get("references", []),
                                "metadata": event.get("metadata", {}),
                            },
                        )
                        return

                    elif event_type == "error":
                        yield await format_sse_event(
                            "error",
                            {"error": event.get("error", "Unknown error occurred")},
                        )
                        return

            except Exception as exc:
                logger.error(
                    "Error in SSE stream (repo=%s session=%s): %s",
                    repo_id,
                    session_id,
                    exc,
                    exc_info=True,
                )
                yield await format_sse_event(
                    "error",
                    {"error": f"Failed to stream response: {str(exc)}"},
                )

        return StreamingResponse(
            generate_sse_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to start streaming chat message (repo=%s session=%s): %s",
            repo_id,
            session_id,
            exc,
            exc_info=True,
        )

        error_message = str(exc)

        async def error_stream():
            yield await format_sse_event(
                "error",
                {"error": f"Failed to start streaming: {error_message}"},
            )

        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
