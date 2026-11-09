"""FastAPI dependency providers for application services."""

from typing import Optional

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.errors import AppError
from core.security import decode_access_token
from services.auth_service import AuthService
from services.chat_service import ChatService
from services.embeddings.base import Embedder
from services.github.oauth import GitHubOAuth
from services.ingestion_service import IngestionService
from services.jobs.queue import JobQueue
from services.mongodb_service import MongoDBService
from services.repository_service import RepositoryService
from services.retrieval_service import RetrievalService
from services.vector_store.base import VectorStore

_bearer = HTTPBearer(auto_error=False)


def get_mongodb_service(request: Request) -> MongoDBService:
    """Return the shared MongoDB service from application state."""
    return request.app.state.mongodb_service


def get_ingestion_service(request: Request) -> IngestionService:
    """Return the shared ingestion service from application state."""
    return request.app.state.ingestion_service


def get_repository_service(request: Request) -> RepositoryService:
    """Return the shared repository service from application state."""
    return request.app.state.repository_service


def get_chat_service(request: Request) -> ChatService:
    """Return the shared chat service from application state."""
    return request.app.state.chat_service


def get_job_queue(request: Request) -> JobQueue:
    """Return the shared ingestion job queue from application state."""
    return request.app.state.job_queue


def get_embedder(request: Request) -> Embedder:
    """Return the shared embedder from application state."""
    return request.app.state.embedder


def get_vector_store(request: Request) -> VectorStore:
    """Return the shared vector store from application state."""
    return request.app.state.vector_store


def get_retrieval_service(request: Request) -> RetrievalService:
    """Return the shared retrieval service from application state."""
    return request.app.state.retrieval_service


def get_auth_service(request: Request) -> AuthService:
    """Return the shared auth service from application state."""
    return request.app.state.auth_service


def get_github_oauth(request: Request) -> GitHubOAuth:
    """Return the shared GitHub OAuth helper from application state."""
    return request.app.state.github_oauth


async def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """Resolve the caller from `Authorization: Bearer`. Raises 401 if missing or invalid."""
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise AppError(
            "Not authenticated",
            code="unauthorized",
            status_code=401,
        )
    payload = decode_access_token(creds.credentials)
    user = await request.app.state.auth_service.get_user(str(payload["sub"]))
    if not user or user.get("status") != "active":
        raise AppError(
            "Not authenticated",
            code="unauthorized",
            status_code=401,
        )
    return user
