"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from api.routes import auth, chat, health, repository
from config.settings import settings
from core.errors import register_exception_handlers
from core.logging import RequestContextMiddleware, setup_logging
from database.mongodb import mongodb
from scripts.create_indexes import apply_indexes
from services.auth_service import AuthService
from services.chat_service import ChatService
from services.embeddings.fake import FakeEmbedder
from services.embeddings.gemini import GeminiEmbedder
from services.github import GitHubClient, GitHubOAuth
from services.ingestion_service import IngestionService
from services.jobs.queue import JobQueue
from services.jobs.worker import IngestionWorker
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
    app.state.job_queue = JobQueue(app.state.mongodb_service.ingestion_requests)
    app.state.repository_service = RepositoryService()
    app.state.retrieval_service = RetrievalService(embedder=embedder, vector_store=store)
    app.state.ingestion_service = IngestionService(
        embedder=embedder,
        vector_store=store,
        job_queue=app.state.job_queue,
    )
    app.state.chat_service = ChatService(embedder=embedder, vector_store=store)
    app.state.job_worker = IngestionWorker(
        app.state.job_queue,
        app.state.ingestion_service,
    )
    github_client = GitHubClient()
    app.state.github_client = github_client
    app.state.github_oauth = GitHubOAuth(client=github_client)
    app.state.auth_service = AuthService(
        mongodb.db["csUsers"],
        mongodb.db["csRefreshTokens"],
    )
    if not settings.jwt_secret or not settings.github_oauth_client_id:
        logger.warning(
            "Auth is not fully configured; /auth/* needs JWT_SECRET and GitHub OAuth env"
        )

    try:
        await apply_indexes(mongodb.db, include_unique=True)
    except PyMongoError:
        logger.exception("Failed to ensure MongoDB indexes")

    stuck = await app.state.mongodb_service.recover_stuck_processing_jobs()
    if stuck:
        logger.info("Recovered %s stuck processing job(s)", stuck)
    recovered = await app.state.mongodb_service.fail_stale_streaming_messages()
    if recovered:
        logger.info(
            "Recovered %s chat session(s) with stale streaming messages",
            recovered,
        )

    worker_task = None
    if settings.ingestion_worker_enabled:
        worker_task = asyncio.create_task(app.state.job_worker.run())

    yield

    logger.info("Shutting down application...")
    if worker_task is not None:
        await app.state.job_worker.stop()
        try:
            await asyncio.wait_for(worker_task, timeout=25)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            worker_task.cancel()

    store = getattr(app.state, "vector_store", None)
    close = getattr(store, "close", None)
    if close is not None:
        await close()
    github_client = getattr(app.state, "github_client", None)
    if github_client is not None:
        await github_client.aclose()
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
app.include_router(auth.router)
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
