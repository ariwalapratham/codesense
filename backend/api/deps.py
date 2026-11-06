"""FastAPI dependency providers for application services."""

from fastapi import Request

from services.chat_service import ChatService
from services.ingestion_service import IngestionService
from services.jobs.queue import JobQueue
from services.mongodb_service import MongoDBService
from services.repository_service import RepositoryService


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
