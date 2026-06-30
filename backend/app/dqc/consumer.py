from __future__ import annotations

import json

from confluent_kafka import Consumer, KafkaException

from app.db import SessionLocal
from app.dqc.config import (
    DQC_CONSUMER_GROUP,
    ENABLE_DQC_DLQ_LOGGING,
    ENABLE_DQC_EVENT_KG_WRITES,
    KAFKA_BOOTSTRAP_SERVERS,
)
from app.dqc.dlq import emit_dlq_observability_log
from app.dqc.event_kg_writer import DQCEventKGWriter
from app.dqc.repositories import save_dqc_dlq_event
from app.dqc.service import process_dqc_event
from app.dqc.topics import DQC_RAW_TOPIC


def get_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": DQC_CONSUMER_GROUP,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )


def _save_dlq(topic: str, event: dict, exc: Exception) -> None:
    db = SessionLocal()
    try:
        row = save_dqc_dlq_event(
            db=db,
            topic=topic,
            payload=event,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        db.commit()

        if ENABLE_DQC_DLQ_LOGGING:
            emit_dlq_observability_log(
                dlq_id=row.id,
                topic=topic,
                payload=event,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        if ENABLE_DQC_EVENT_KG_WRITES:
            writer = DQCEventKGWriter()
            try:
                writer.write_dlq_event(
                    topic=topic,
                    dlq_id=row.id,
                    payload=event,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            finally:
                writer.close()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def handle_message(topic: str, event: dict) -> None:
    db = SessionLocal()
    try:
        if topic != DQC_RAW_TOPIC:
            raise ValueError(f"Unsupported topic: {topic}")
        process_dqc_event(db, topic, event)
    finally:
        db.close()


def run_consumer() -> None:
    consumer = get_consumer()
    consumer.subscribe([DQC_RAW_TOPIC])
    print(f"DQC consumer started on topic {DQC_RAW_TOPIC}. Press Ctrl+C to stop.")

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
                print(f"Processed DQC message from {topic}")
            except Exception as exc:
                print(f"DQC message sent to DLQ from {topic}: {exc}")
                try:
                    parsed = json.loads(msg.value().decode("utf-8"))
                except Exception:
                    parsed = {"raw_value": str(msg.value())}
                _save_dlq(topic, parsed, exc)
                consumer.commit(msg)
    finally:
        consumer.close()
