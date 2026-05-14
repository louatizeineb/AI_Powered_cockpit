from __future__ import annotations

from _bootstrap import bootstrap_eventing

bootstrap_eventing()

from confluent_kafka.admin import AdminClient, NewTopic

from app.eventing.config import KAFKA_BOOTSTRAP_SERVERS
from app.eventing.topics import ALL_TOPICS


def main() -> None:
    admin = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})

    topics = [
        NewTopic(topic, num_partitions=1, replication_factor=1)
        for topic in ALL_TOPICS
    ]

    futures = admin.create_topics(topics)

    for topic, future in futures.items():
        try:
            future.result()
            print(f"Created topic: {topic}")
        except Exception as exc:
            if "already exists" in str(exc).lower():
                print(f"Topic already exists: {topic}")
            else:
                print(f"Failed to create topic {topic}: {exc}")


if __name__ == "__main__":
    main()
