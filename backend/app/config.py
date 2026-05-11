from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Data Quality Cockpit Backend"
    APP_ENV: str = "dev"

    POSTGRES_URL: str

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str

    MARQUEZ_URL: str = "http://localhost:5000"
    MARQUEZ_LINEAGE_ENDPOINT: str = "http://localhost:5000/api/v1/lineage"

    OPENLINEAGE_PRODUCER: str = "data-quality-cockpit/postgres-link-bootstrap"
    OPENLINEAGE_JOB_NAMESPACE: str = "datagalaxy.processing"
    OPENLINEAGE_DATASET_NAMESPACE: str = "datagalaxy://catalog"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()