"""Configuration management using Pydantic Settings."""

from typing import Optional

from pydantic import Field, field_validator, model_validator
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

    # Repository Processing Configuration
    temp_repos_path: str = "../temp_repos"

    # CORS — locked to this origin (not *)
    frontend_origin: str = Field(default="http://localhost:3000")

    # Qdrant — empty URL means in-process MemoryVectorStore (lost on restart)
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "codesense_chunks"

    # Ingest size limits (checked before clone / while walking files)
    max_repo_size_mb: int = Field(default=50, ge=1)
    max_file_size_kb: int = Field(default=256, ge=1)
    ingestion_worker_enabled: bool = True

    #JWT_SECRET and OAuth client values are required to issue tokens.
    jwt_secret: Optional[str] = None
    access_token_ttl_min: int = Field(default=15, ge=1)
    refresh_token_ttl_days: int = Field(default=30, ge=1)
    github_oauth_client_id: Optional[str] = None
    github_oauth_client_secret: Optional[str] = None
    auth_refresh_cookie_name: str = "codesense_refresh"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"

    # Optional Configuration
    github_token: Optional[str] = None
    gitlab_token: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator(
        "qdrant_url",
        "qdrant_api_key",
        "jwt_secret",
        "github_oauth_client_id",
        "github_oauth_client_secret",
        mode="before",
    )
    @classmethod
    def blank_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_strength(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if len(value) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value

    @field_validator("auth_cookie_samesite")
    @classmethod
    def normalize_cookie_samesite(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be lax, strict, or none")
        return normalized

    @field_validator("frontend_origin")
    @classmethod
    def normalize_frontend_origin(cls, value: str) -> str:
        origin = value.strip().rstrip("/")
        if not origin or origin == "*":
            raise ValueError("FRONTEND_ORIGIN must be a concrete origin, not empty or *")
        if not (origin.startswith("http://") or origin.startswith("https://")):
            raise ValueError("FRONTEND_ORIGIN must start with http:// or https://")
        return origin

    @model_validator(mode="after")
    def cookie_samesite_none_requires_secure(self):
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError("AUTH_COOKIE_SAMESITE=none requires AUTH_COOKIE_SECURE=true")
        return self


# Global settings instance
settings = Settings()
