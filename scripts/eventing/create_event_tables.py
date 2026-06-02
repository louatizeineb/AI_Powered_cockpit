from __future__ import annotations

from _bootstrap import bootstrap_eventing

bootstrap_eventing()

from backend.app.db import pg_engine
from backend.app.eventing.models import Base


def main() -> None:
    Base.metadata.create_all(bind=pg_engine)
    print("Event tables created or already exist.")


if __name__ == "__main__":
    main()
