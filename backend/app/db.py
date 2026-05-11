from contextlib import contextmanager

from neo4j import GraphDatabase
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings


settings = get_settings()

pg_engine: Engine = create_engine(
    settings.POSTGRES_URL,
    pool_pre_ping=True,
)

neo4j_driver = GraphDatabase.driver(
    settings.NEO4J_URI,
    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    connection_timeout=60,
    max_connection_lifetime=3600,
)


@contextmanager
def postgres_conn():
    with pg_engine.connect() as conn:
        yield conn


@contextmanager
def neo4j_session():
    with neo4j_driver.session() as session:
        yield session


def check_postgres() -> bool:
    try:
        with postgres_conn() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def check_neo4j() -> bool:
    try:
        with neo4j_session() as session:
            session.run("RETURN 1 AS ok").single()
        return True
    except Exception:
        return False


def close_neo4j() -> None:
    neo4j_driver.close()