from __future__ import annotations

from _bootstrap import bootstrap_eventing

bootstrap_eventing()

from backend.app.eventing.event_kg_writer import EventKGWriter


def main() -> None:
    writer = EventKGWriter()
    try:
        writer.ensure_constraints()
        print("Event Knowledge Graph constraints created.")
    finally:
        writer.close()


if __name__ == "__main__":
    main()
