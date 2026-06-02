from __future__ import annotations

from _bootstrap import bootstrap_eventing

bootstrap_eventing()

from backend.app.eventing.producer import publish_event
from backend.app.eventing.sample_events import SAMPLE_DATAQUALITY_EVENT, SAMPLE_PIPELINE_EVENT
from backend.app.eventing.topics import DATAQUALITY_RAW_TOPIC, PIPELINE_RAW_TOPIC


def main() -> None:
    publish_event(DATAQUALITY_RAW_TOPIC, SAMPLE_DATAQUALITY_EVENT, key="dq-test-001")
    print("Sent sample data quality event.")

    publish_event(PIPELINE_RAW_TOPIC, SAMPLE_PIPELINE_EVENT, key="run-test-001")
    print("Sent sample pipeline event.")


if __name__ == "__main__":
    main()
