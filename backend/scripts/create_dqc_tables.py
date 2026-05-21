from __future__ import annotations

from _bootstrap import bootstrap_backend

bootstrap_backend()

from backend.app.db import Base, pg_engine
from backend.app.dqc import models  # noqa: F401 - imports table metadata


if __name__ == "__main__":
    print("Creating DQC PostgreSQL tables...")
    Base.metadata.create_all(bind=pg_engine)
    print("DQC tables created.")
