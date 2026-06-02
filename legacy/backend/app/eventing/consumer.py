from __future__ import annotations

import json

from confluent_kafka import Consumer, KafkaException

from backend.app.db import SessionLocal
from backend.app.eventing.config import ENABLE_EVENT_KG_WRITES, EVENT_CONSUMER_GROUP, KAFKA_BOOTSTRAP_SERVERS
from backend.app.eventing.event_kg_writer import EventKGWriter
from backend.app.eventing.repositories import save_dlq_event
from backend.app.eventing.service import process_dataquality_event, process_pipeline_event
from backend.app.eventing.topics import DATAQUALITY_RAW_TOPIC, PIPELINE_RAW_TOPIC


def get_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": EVENT_CONSUMER_GROUP,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )


def _save_dlq(topic: str, event: dict, exc: Exception) -> None:
    db = SessionLocal()
    try:
        row = save_dlq_event(
            db=db,
            topic=topic,
            payload=event,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        if ENABLE_EVENT_KG_WRITES:
            writer = EventKGWriter()
            try:
                writer.write_dlq_event(topic=topic, dlq_id=row.id, payload=event, error_type=type(exc).__name__, error_message=str(exc))
            finally:
                writer.close()
    finally:
        db.close()


def handle_message(topic: str, event: dict) -> None:
    db = SessionLocal()
    try:
        if topic == DATAQUALITY_RAW_TOPIC:
            process_dataquality_event(db, topic, event)
        elif topic == PIPELINE_RAW_TOPIC:
            process_pipeline_event(db, topic, event)
        else:
            raise ValueError(f"Unsupported topic: {topic}")
    finally:
        db.close()


def run_consumer() -> None:
    consumer = get_consumer()
    consumer.subscribe([DATAQUALITY_RAW_TOPIC, PIPELINE_RAW_TOPIC])
    print("Event consumer started. Press Ctrl+C to stop.")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                raise KafkaException(msg.error())

            topic = msg.topic()
            try:
                event = json.loads(msg.value().decode("utf-8"))
                handle_message(topic, event)
                consumer.commit(msg)
                print(f"Processed message from {topic}")
            except Exception as exc:
                print(f"Message sent to DLQ from {topic}: {exc}")
                try:
                    parsed = json.loads(msg.value().decode("utf-8"))
                except Exception:
                    parsed = {"raw_value": str(msg.value())}
                _save_dlq(topic, parsed, exc)
                consumer.commit(msg)
    finally:
        consumer.close()
