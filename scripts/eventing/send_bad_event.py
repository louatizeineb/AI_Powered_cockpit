from __future__ import annotations

from _bootstrap import bootstrap_eventing

bootstrap_eventing()

from backend.app.eventing.producer import publish_event
from backend.app.eventing.sample_events import BAD_DATAQUALITY_EVENT
from backend.app.eventing.topics import DATAQUALITY_RAW_TOPIC


def main() -> None:
    publish_event(DATAQUALITY_RAW_TOPIC, BAD_DATAQUALITY_EVENT, key="dq-bad-001")
    print("Sent invalid data quality event. It should go to event_dlq and DLQEvent in Event KG.")


if __name__ == "__main__":
    main()
