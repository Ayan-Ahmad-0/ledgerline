import os
from functools import lru_cache
from typing import Optional, Any
from pydantic import Field

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict  # type: ignore
    HAS_PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseModel
    HAS_PYDANTIC_SETTINGS = False


if HAS_PYDANTIC_SETTINGS:
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore"
        )

        PROJECT_NAME: str = "Ledgerline"
        ENV: str = Field(default="development", description="development, staging, production")
        DEBUG: bool = False

        DATABASE_URL: str = Field(
            default="postgresql+asyncpg://postgres:postgres@localhost:5432/ledgerline",
            description="Async database connection string"
        )
        SYNC_DATABASE_URL: Optional[str] = Field(
            default=None,
            description="Sync database connection string for Alembic migrations"
        )
        DB_POOL_SIZE: int = 10
        DB_MAX_OVERFLOW: int = 20
        DB_POOL_TIMEOUT: int = 30

        R2_ACCOUNT_ID: Optional[str] = None
        R2_ACCESS_KEY_ID: Optional[str] = None
        R2_SECRET_ACCESS_KEY: Optional[str] = None
        R2_BUCKET_NAME: str = "ledgerline-documents"
        R2_ENDPOINT_URL: Optional[str] = None

        API_KEY_HEADER: str = "X-API-Key"
        API_KEYS: list[str] = ["dev-ledgerline-key"]

        SENTRY_DSN: Optional[str] = None

        CANARY_SAMPLE_SIZE: int = 50
        CANARY_MAX_F1_DROP: float = 2.0
        MAX_PARK_RATE: float = 0.03
        # LLM Provider Configuration
        GEMINI_API_KEY: Optional[str] = None

        EXTRACTOR_VERSION: str = "v1.0.0"
        PROMPT_VERSION: str = "v1"
        CALIBRATION_ECE_TARGET: float = 0.10

else:
    from pydantic import BaseModel

    class Settings(BaseModel):
        PROJECT_NAME: str = os.getenv("PROJECT_NAME", "Ledgerline")
        ENV: str = os.getenv("ENV", "development")
        DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1")

        DATABASE_URL: str = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/ledgerline"
        )
        SYNC_DATABASE_URL: Optional[str] = os.getenv("SYNC_DATABASE_URL")
        DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))
        DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

        R2_ACCOUNT_ID: Optional[str] = os.getenv("R2_ACCOUNT_ID")
        R2_ACCESS_KEY_ID: Optional[str] = os.getenv("R2_ACCESS_KEY_ID")
        R2_SECRET_ACCESS_KEY: Optional[str] = os.getenv("R2_SECRET_ACCESS_KEY")
        R2_BUCKET_NAME: str = os.getenv("R2_BUCKET_NAME", "ledgerline-documents")
        R2_ENDPOINT_URL: Optional[str] = os.getenv("R2_ENDPOINT_URL")

        API_KEY_HEADER: str = os.getenv("API_KEY_HEADER", "X-API-Key")
        API_KEYS: list[str] = ["dev-ledgerline-key"]

        SENTRY_DSN: Optional[str] = os.getenv("SENTRY_DSN")

        CANARY_SAMPLE_SIZE: int = int(os.getenv("CANARY_SAMPLE_SIZE", "50"))
        CANARY_MAX_F1_DROP: float = float(os.getenv("CANARY_MAX_F1_DROP", "2.0"))
        MAX_PARK_RATE: float = float(os.getenv("MAX_PARK_RATE", "0.03"))
        # LLM Provider Configuration
        GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")

        EXTRACTOR_VERSION: str = os.getenv("EXTRACTOR_VERSION", "v1.0.0")
        PROMPT_VERSION: str = os.getenv("PROMPT_VERSION", "v1")
        CALIBRATION_ECE_TARGET: float = float(os.getenv("CALIBRATION_ECE_TARGET", "0.10"))


@lru_cache()
def get_settings() -> Settings:
    return Settings()
