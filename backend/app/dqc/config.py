from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env.eventing", override=False)

# Kafka
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DQC_CONSUMER_GROUP = os.getenv("DQC_CONSUMER_GROUP", "dqc-event-consumer")
DQC_ENVIRONMENT = os.getenv("DQC_ENVIRONMENT", "test")

# Feature flags
ENABLE_DQC_CATALOG_RESOLUTION = os.getenv("ENABLE_DQC_CATALOG_RESOLUTION", "true").lower() == "true"
ENABLE_DQC_EVENT_KG_WRITES = os.getenv("ENABLE_DQC_EVENT_KG_WRITES", "true").lower() == "true"
ENABLE_DQC_DLQ_LOGGING = os.getenv("ENABLE_DQC_DLQ_LOGGING", "true").lower() == "true"

# Schemas
DQC_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# Separate Event Knowledge Graph Neo4j instance
EVENT_NEO4J_URI = os.getenv("EVENT_NEO4J_URI", "bolt://127.0.0.1:7688")
EVENT_NEO4J_USER = os.getenv("EVENT_NEO4J_USER", "neo4j")
EVENT_NEO4J_PASSWORD = os.getenv("EVENT_NEO4J_PASSWORD", "change_me")

# Structured DLQ logs read by Logstash
DQC_LOG_DIR = Path(os.getenv("DQC_LOG_DIR", "logs/dqc"))
DQC_DLQ_LOG_FILE = DQC_LOG_DIR / "dqc_dlq.log"

# Optional direct Elasticsearch indexing
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
