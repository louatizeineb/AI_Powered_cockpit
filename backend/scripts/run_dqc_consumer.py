from __future__ import annotations

from _bootstrap import bootstrap_backend

bootstrap_backend()

from backend.app.dqc.consumer import run_consumer


if __name__ == "__main__":
    run_consumer()
