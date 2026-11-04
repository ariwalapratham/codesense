"""FastAPI dependency providers for application services."""

from fastapi import Request

from services.chat_service import ChatService
from services.embeddings.base import Embedder
from services.ingestion_service import IngestionService
from services.mongodb_service import MongoDBService
from services.repository_service import RepositoryService
from services.vector_store.base import VectorStore


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


def get_embedder(request: Request) -> Embedder:
    """Return the shared embedder from application state."""
    return request.app.state.embedder


def get_vector_store(request: Request) -> VectorStore:
    """Return the shared vector store from application state."""
    return request.app.state.vector_store
