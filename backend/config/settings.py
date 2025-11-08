"""Configuration management using Pydantic Settings."""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore[reportMissingImports]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Gemini Configuration
    gemini_api_key: str
    gemini_embedding_model: str = "models/embedding-001"
    gemini_llm_model: str = "gemini-pro"

    # MongoDB Configuration
    mongodb_url: str = "mongodb://localhost:27017"
    db_name: str = "codesense"

    # Vector Store Configuration
    vector_store_path: str = "./vector_stores"

    # Optional Configuration
    github_token: Optional[str] = None
    gitlab_token: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# Global settings instance
settings = Settings()
