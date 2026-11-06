"""FastAPI application entry point."""

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
from services.ingestion_service import IngestionService
from services.jobs.queue import JobQueue
from services.mongodb_service import MongoDBService
from services.repository_service import RepositoryService

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start shared services, ensure indexes, then close Mongo on shutdown."""
    logger.info("Starting application...")
    logger.info("CORS origin: %s", settings.frontend_origin)
    app.state.mongodb_service = MongoDBService()
    app.state.job_queue = JobQueue(app.state.mongodb_service.ingestion_requests)
    app.state.ingestion_service = IngestionService()
    app.state.repository_service = RepositoryService()
    app.state.chat_service = ChatService()

    try:
        await apply_indexes(mongodb.db, include_unique=True)
    except PyMongoError:
        logger.exception("Failed to ensure MongoDB indexes")
    else:
        recovered = await app.state.mongodb_service.fail_stale_streaming_messages()
        if recovered:
            logger.info(
                "Recovered %s chat session(s) with stale streaming messages",
                recovered,
            )

    yield

    logger.info("Shutting down application...")
    await mongodb.close()
    logger.info("MongoDB connection closed")


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
