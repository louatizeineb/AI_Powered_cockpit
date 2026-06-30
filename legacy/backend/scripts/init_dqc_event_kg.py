from __future__ import annotations

from _bootstrap import bootstrap_backend

bootstrap_backend()

from backend.app.dqc.event_kg_writer import DQCEventKGWriter


if __name__ == "__main__":
    writer = DQCEventKGWriter()
    try:
        writer.ensure_constraints()
        print("DQC Event KG constraints created.")
    finally:
        writer.close()
