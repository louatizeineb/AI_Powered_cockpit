from __future__ import annotations

import argparse

from _bootstrap import bootstrap_backend

bootstrap_backend()

from backend.app.dqc.producer import publish_event
from backend.app.dqc.synthetic_generator import make_event_batch, make_valid_event
from backend.app.dqc.topics import DQC_RAW_TOPIC


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--valid-only", action="store_true")
    args = parser.parse_args()

    events = [make_valid_event() for _ in range(args.count)] if args.valid_only else make_event_batch(args.count)
    for event in events:
        key = event.get("metadata", {}).get("eventId")
        publish_event(DQC_RAW_TOPIC, event, key=key)
        print(f"Published {key} to {DQC_RAW_TOPIC}")


if __name__ == "__main__":
    main()
