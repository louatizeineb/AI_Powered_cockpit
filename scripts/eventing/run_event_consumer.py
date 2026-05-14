from __future__ import annotations

from _bootstrap import bootstrap_eventing

bootstrap_eventing()

from backend.app.eventing.consumer import run_consumer


if __name__ == "__main__":
    run_consumer()
