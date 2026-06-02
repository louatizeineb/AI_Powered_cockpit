from __future__ import annotations

import socket

from _bootstrap import bootstrap_backend

bootstrap_backend()

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from backend.app.dqc.config import KAFKA_BOOTSTRAP_SERVERS
from backend.app.dqc.topics import ALL_DQC_TOPICS


def _first_bootstrap_address(bootstrap_servers: str) -> tuple[str, int]:
    server = bootstrap_servers.split(",", 1)[0].strip()
    if "://" in server:
        server = server.split("://", 1)[1]

    host, _, port = server.rpartition(":")
    return host or "localhost", int(port or 9092)


def _assert_kafka_port_open() -> None:
    host, port = _first_bootstrap_address(KAFKA_BOOTSTRAP_SERVERS)
    try:
        with socket.create_connection((host, port), timeout=3):
            return
    except OSError as exc:
        raise SystemExit(
            "Kafka broker is not reachable at "
            f"{KAFKA_BOOTSTRAP_SERVERS}. Start Redpanda/Kafka first, then rerun this script.\n"
            "For this repo, try: docker compose -f docker/docker-compose.yml up -d redpanda"
        ) from exc


def main() -> None:
    _assert_kafka_port_open()

    admin = AdminClient(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "socket.timeout.ms": 5000,
            "message.timeout.ms": 5000,
        }
    )

    try:
        admin.list_topics(timeout=5)
    except Exception as exc:
        raise SystemExit(
            f"Connected to {KAFKA_BOOTSTRAP_SERVERS}, but Kafka metadata was unavailable."
        ) from exc

    topics = [NewTopic(topic, num_partitions=1, replication_factor=1) for topic in ALL_DQC_TOPICS]
    futures = admin.create_topics(topics, operation_timeout=5, request_timeout=10)
    for topic, future in futures.items():
        try:
            future.result(timeout=10)
            print(f"Created topic: {topic}")
        except KafkaException as exc:
            error = exc.args[0] if exc.args else None
            if isinstance(error, KafkaError) and error.code() == KafkaError.TOPIC_ALREADY_EXISTS:
                print(f"Topic already exists: {topic}")
                continue
            raise
        except Exception as exc:
            print(f"Topic {topic}: {exc}")


if __name__ == "__main__":
    main()
