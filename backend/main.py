"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import chat, repository
from database.mongodb import mongodb
from services.chat_service import ChatService
from services.ingestion_service import IngestionService
from services.mongodb_service import MongoDBService
from services.repository_service import RepositoryService

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application.

    Handles startup and shutdown events.
    """
    logger.info("Starting application...")
    app.state.mongodb_service = MongoDBService()
    app.state.ingestion_service = IngestionService()
    app.state.repository_service = RepositoryService()
    app.state.chat_service = ChatService()

    yield

    # Shutdown
    logger.info("Shutting down application...")
    await mongodb.close()
    logger.info("MongoDB connection closed")


# Create FastAPI application
app = FastAPI(
    title="CodeSense API",
    description="Code search and analysis API with RAG capabilities",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Configure specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chat.router)
app.include_router(repository.router)


@app.get("/", tags=["root"])
async def root():
    """
    Root endpoint.

    Returns:
        API information
    """
    return {
        "name": "CodeSense API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health", tags=["health"])
async def health():
    """
    Health check endpoint.

    Returns:
        Health status
    """
    return {
        "status": "healthy",
        "service": "codesense-api"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
