"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from api.routes import chat, health, repository
from config.settings import settings
from core.errors import register_exception_handlers
from core.logging import RequestContextMiddleware, setup_logging
from database.mongodb import mongodb
from scripts.create_indexes import apply_indexes
from services.chat_service import ChatService
from services.embeddings.fake import FakeEmbedder
from services.embeddings.gemini import GeminiEmbedder
from services.ingestion_service import IngestionService
from services.mongodb_service import MongoDBService
from services.repository_service import RepositoryService
from services.retrieval_service import RetrievalService
from services.vector_store.memory import MemoryVectorStore

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start shared services, ensure indexes, then close Mongo on shutdown."""
    logger.info("Starting application...")
    logger.info("CORS origin: %s", settings.frontend_origin)
    embedder = GeminiEmbedder() if settings.gemini_api_key else FakeEmbedder()
    store = await _build_vector_store()
    app.state.embedder = embedder
    app.state.vector_store = store
    app.state.mongodb_service = MongoDBService()
    app.state.repository_service = RepositoryService()
    app.state.retrieval_service = RetrievalService(embedder=embedder, vector_store=store)
    app.state.ingestion_service = IngestionService(embedder=embedder, vector_store=store)
    app.state.chat_service = ChatService(embedder=embedder, vector_store=store)

    try:
        await apply_indexes(mongodb.db, include_unique=True)
    except PyMongoError:
        logger.exception("Failed to ensure MongoDB indexes")

    yield

    logger.info("Shutting down application...")
    store = getattr(app.state, "vector_store", None)
    close = getattr(store, "close", None)
    if close is not None:
        await close()
    await mongodb.close()
    logger.info("MongoDB connection closed")


async def _build_vector_store():
    if not settings.qdrant_url:
        return MemoryVectorStore()
    from services.vector_store.qdrant import QdrantVectorStore

    store = QdrantVectorStore()
    try:
        await asyncio.wait_for(store.ensure_collection(), timeout=5)
    except Exception:
        logger.exception("Failed to ensure Qdrant collection")
    return store


app = FastAPI(
    title="CodeSense API",
    description="Code search and analysis API with RAG capabilities",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
)
app.add_middleware(RequestContextMiddleware)

register_exception_handlers(app)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(repository.router)


@app.get("/", tags=["root"])
async def root():
    """API information."""
    return {
        "name": "CodeSense API",
        "version": "1.0.0",
        "status": "running",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
