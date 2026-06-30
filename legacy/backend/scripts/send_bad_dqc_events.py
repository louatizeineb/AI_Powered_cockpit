from __future__ import annotations

from _bootstrap import bootstrap_backend

bootstrap_backend()

from backend.app.dqc.producer import publish_event
from backend.app.dqc.synthetic_generator import (
    make_bad_counts_event,
    make_bad_integer_event,
    make_missing_app_code_event,
    make_missing_object_name_event,
)
from backend.app.dqc.topics import DQC_RAW_TOPIC


if __name__ == "__main__":
    events = [
        make_missing_app_code_event(),
        make_missing_object_name_event(),
        make_bad_counts_event(),
        make_bad_integer_event(),
    ]
    for event in events:
        key = event.get("metadata", {}).get("eventId")
        publish_event(DQC_RAW_TOPIC, event, key=key)
        print(f"Published bad event {key} to {DQC_RAW_TOPIC}")
