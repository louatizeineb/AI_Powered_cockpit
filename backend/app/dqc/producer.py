from __future__ import annotations

import json

from confluent_kafka import Producer

from backend.app.dqc.config import KAFKA_BOOTSTRAP_SERVERS


def get_producer() -> Producer:
    return Producer({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})


def publish_event(topic: str, event: dict, key: str | None = None) -> None:
    producer = get_producer()
    producer.produce(topic=topic, key=key, value=json.dumps(event).encode("utf-8"))
    producer.flush()
