from __future__ import annotations

import os
from pathlib import Path

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
EVENT_CONSUMER_GROUP = os.getenv("EVENT_CONSUMER_GROUP", "data-lineage-event-consumer")
EVENT_ENVIRONMENT = os.getenv("EVENT_ENVIRONMENT", "test")

ENABLE_EVENT_CATALOG_RESOLUTION = os.getenv(
    "ENABLE_EVENT_CATALOG_RESOLUTION", "true"
).lower() == "true"

ENABLE_EVENT_KG_WRITES = os.getenv(
    "ENABLE_EVENT_KG_WRITES", "true"
).lower() == "true"

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

EVENT_NEO4J_URI = os.getenv("EVENT_NEO4J_URI", "bolt://127.0.0.1:7688")
EVENT_NEO4J_USER = os.getenv("EVENT_NEO4J_USER", "neo4j")
EVENT_NEO4J_PASSWORD = os.getenv("EVENT_NEO4J_PASSWORD", "change_me")
