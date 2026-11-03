"""Configuration management using Pydantic Settings."""

from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore[reportMissingImports]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Gemini Configuration
    gemini_api_key: Optional[str] = None
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_llm_model: str = "gemini-2.5-flash"
    gemini_llm_fallback_model: str = "gemini-2.5-flash-lite"

    # MongoDB Configuration
    mongodb_url: str = "mongodb://localhost:27017"
    db_name: str = "codesense"

    # Vector Store Configuration
    vector_store_path: str = "../vector_stores"

    # Repository Processing Configuration
    temp_repos_path: str = "../temp_repos"

    # CORS — locked to this origin (not *)
    frontend_origin: str = Field(default="http://localhost:3000")

    # Optional Configuration
    github_token: Optional[str] = None
    gitlab_token: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("frontend_origin")
    @classmethod
    def normalize_frontend_origin(cls, value: str) -> str:
        origin = value.strip().rstrip("/")
        if not origin or origin == "*":
            raise ValueError("FRONTEND_ORIGIN must be a concrete origin, not empty or *")
        if not (origin.startswith("http://") or origin.startswith("https://")):
            raise ValueError("FRONTEND_ORIGIN must start with http:// or https://")
        return origin


# Global settings instance
settings = Settings()
