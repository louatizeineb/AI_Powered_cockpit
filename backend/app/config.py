from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    APP_NAME: str = "Data Quality Cockpit Backend"
    APP_ENV: str = "dev"

    POSTGRES_URL: str = ""
    migration_v2_postgres_url: str = ""
    migration_v2_workflow_version: str = "1.0.0"
    migration_v2_env_config_path: str = "configs/migration_v2/local_env.yaml"
    migration_v2_contract_path: str = "backend/app/migration_v2/contracts/datagalaxy_athena_v1.yaml"
    REDIS_URL: str = ""
    lineage_search_cache_ttl_seconds: int = 15 * 60
    lineage_expansion_cache_ttl_seconds: int = 60 * 60

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str

    schema_intelligence_neo4j_uri: str = "bolt://127.0.0.1:7690"
    schema_intelligence_neo4j_user: str = "neo4j"
    schema_intelligence_neo4j_password: str = ""
    schema_intelligence_neo4j_database: str = "neo4j"

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
    azure_openai_timeout_seconds: float = 90.0

    llm_provider: str = "openai"
    llm_disable_azure_fallback: bool = True
    llm_run_max_calls: int = 20
    llm_max_prompt_chars: int = 8000
    llm_max_completion_tokens: int = 700
    llm_reasoning_effort: str = "low"
    llm_min_seconds_between_calls: float = 0.25
    llm_rag_object_rows: int = 4
    llm_rag_relationship_neighbors: int = 6
    llm_rag_lineage_examples: int = 2
    llm_rag_similar_decisions: int = 4
    llm_rag_schema_columns: int = 4
    llm_rag_provenance_events: int = 3
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-5.4-mini"
    openai_base_url: str = ""
    openai_timeout_seconds: float = 90.0

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
