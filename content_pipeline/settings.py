from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    DATABASE_URL: PostgresDsn
    EMBEDDINGS_MODEL: str = "text-embedding-3-small"
    EMBEDDINGS_BASE_URL: str | None = None
    EMBEDDING_DIMS: int = 1536
    MEM0_LLM_MODEL: str = "claude-haiku-5-5"
    MEM0_COLLECTION: str = "client_editorial_memory"
