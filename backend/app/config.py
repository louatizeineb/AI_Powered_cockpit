from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    APP_NAME: str = "Data Quality Cockpit Backend"
    APP_ENV: str = "dev"

    POSTGRES_URL: str = ""
    REDIS_URL: str = ""
    lineage_search_cache_ttl_seconds: int = 15 * 60
    lineage_expansion_cache_ttl_seconds: int = 60 * 60

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str

    MARQUEZ_URL: str = "http://localhost:5000"
    MARQUEZ_LINEAGE_ENDPOINT: str = "http://localhost:5000/api/v1/lineage"

    OPENLINEAGE_PRODUCER: str = "data-quality-cockpit/postgres-link-bootstrap"
    OPENLINEAGE_JOB_NAMESPACE: str = "datagalaxy.processing"
    OPENLINEAGE_DATASET_NAMESPACE: str = "datagalaxy://catalog"

    catalog_source_table: str = "source"
    catalog_container_table: str = "container"
    catalog_structure_table: str = "structure"
    catalog_field_table: str = "field"
    dqc_default_table: str = "DQC"

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_chat_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"

    embedding_dim: int = 1536
    embedding_provider: str = "local_hash"
    embedding_batch_size: int = 128

    dqc_agent_mode: str = "fixed_workflow"
    dqc_high_confidence: int = 85
    dqc_medium_confidence: int = 65

    log_level: str = "INFO"
    pipeline_log_to_db: bool = True
    upload_dir: str = "./storage/uploads"
    dqc_upload_max_bytes: int = 25 * 1024 * 1024
    dqc_upload_chunk_bytes: int = 1024 * 1024
    dqc_database_max_rows: int = 10000

    model_config = SettingsConfigDict(
        env_file=(
            BACKEND_DIR / ".env",
            BACKEND_DIR / ".env.agent_and_resolution.additions",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
