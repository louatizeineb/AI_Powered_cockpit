from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


def normalize_postgres_connection_string(value: str) -> str:
    """Convert SQLAlchemy driver URLs to the libpq form used by psycopg."""

    return value.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


@contextmanager
def postgres_checkpointer(connection_string: str, *, setup: bool = False) -> Iterator[object]:
    """Yield LangGraph's native PostgresSaver without leaking its connection."""

    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except ImportError as exc:
        raise RuntimeError(
            "LangGraph PostgreSQL checkpoint support is not installed. "
            "Install backend/requirements.txt."
        ) from exc

    normalized = normalize_postgres_connection_string(connection_string)
    with PostgresSaver.from_conn_string(normalized) as saver:
        if setup:
            saver.setup()
        yield saver
