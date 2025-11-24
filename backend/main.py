"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import chat, ingestion, repository
from database.mongodb import mongodb

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Lifespan context manager for FastAPI application.

    Handles startup and shutdown events.

    Args:
        _app: FastAPI application instance (required by FastAPI but unused)
    """
    # Startup
    logger.info("Starting application...")

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
app.include_router(ingestion.router)
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
