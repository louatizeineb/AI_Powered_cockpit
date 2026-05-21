from __future__ import annotations

from typing import Any

from elasticsearch import Elasticsearch

from backend.app.dqc.config import ELASTICSEARCH_URL


def get_elasticsearch_client() -> Elasticsearch:
    return Elasticsearch(ELASTICSEARCH_URL)


def index_dlq_log(document: dict[str, Any]) -> None:
    client = get_elasticsearch_client()
    client.index(index="dqc-dlq-logs-direct", document=document)
