from __future__ import annotations

from neo4j import GraphDatabase

from backend.app.dqc.config import EVENT_NEO4J_PASSWORD, EVENT_NEO4J_URI, EVENT_NEO4J_USER


class DQCEventKGWriter:
    """Writes only to the separate Event Knowledge Graph Neo4j instance."""

    def __init__(self):
        self.driver = GraphDatabase.driver(
            EVENT_NEO4J_URI,
            auth=(EVENT_NEO4J_USER, EVENT_NEO4J_PASSWORD),
        )

    def close(self) -> None:
        self.driver.close()

    def ensure_constraints(self) -> None:
        queries = [
            "CREATE CONSTRAINT dqc_event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.event_id IS UNIQUE",
            "CREATE CONSTRAINT dqc_result_id IF NOT EXISTS FOR (n:DataQualityCheckResult) REQUIRE n.result_id IS UNIQUE",
            "CREATE CONSTRAINT dqc_catalog_ref_key IF NOT EXISTS FOR (n:CatalogReference) REQUIRE n.reference_key IS UNIQUE",
            "CREATE CONSTRAINT dqc_dlq_id IF NOT EXISTS FOR (n:DLQEvent) REQUIRE n.dlq_id IS UNIQUE",
            "CREATE CONSTRAINT dqc_schema IF NOT EXISTS FOR (n:SchemaContract) REQUIRE (n.schema_id, n.version) IS UNIQUE",
            "CREATE CONSTRAINT dqc_topic IF NOT EXISTS FOR (n:Topic) REQUIRE n.name IS UNIQUE",
        ]
        with self.driver.session() as session:
            for query in queries:
                session.run(query).consume()

    def write_dataquality_result(self, *, topic: str, event_store_id: int, dq_result_id: int, normalized: dict, resolution: dict) -> None:
        dq = normalized["dq_result"]
        query = """
        MERGE (e:Event {event_id: $event_id})
        SET e.event_store_id = $event_store_id,
            e.event_family = 'data_quality',
            e.event_type = $event_type,
            e.schema_id = $schema_id,
            e.schema_version = $schema_version,
            e.source_system = $source_system,
            e.correlation_id = $correlation_id,
            e.received_at = datetime()

        MERGE (t:Topic {name: $topic})
        MERGE (e)-[:ARRIVED_ON]->(t)

        MERGE (s:SchemaContract {schema_id: $schema_id, version: $schema_version})
        SET s.name = $event_type
        MERGE (e)-[:VALIDATED_AGAINST]->(s)

        MERGE (q:DataQualityCheckResult {result_id: $dq_result_id})
        SET q.id_ref = $id_ref,
            q.application_code = $application_code,
            q.controlled_object_name = $controlled_object_name,
            q.controlled_object_type = $controlled_object_type,
            q.control_name = $control_name,
            q.quality_dimension = $quality_dimension,
            q.acceptance_threshold = $acceptance_threshold,
            q.execution_timestamp = $execution_timestamp,
            q.business_date = $business_date,
            q.controlled_item_count = $controlled_item_count,
            q.ok_count = $ok_count,
            q.ko_count = $ko_count,
            q.control_tool = $control_tool,
            q.computed_score = $computed_score,
            q.quality_status = $quality_status
        MERGE (e)-[:NORMALIZED_AS]->(q)

        MERGE (ref:CatalogReference {reference_key: $catalog_reference_key})
        SET ref.matched_node_id = $matched_node_id,
            ref.matched_label = $matched_label,
            ref.match_method = $match_method,
            ref.confidence = $confidence,
            ref.status = $match_status,
            ref.application_code = $application_code,
            ref.object_name = $controlled_object_name,
            ref.object_type = $controlled_object_type
        MERGE (q)-[:REFERS_TO]->(ref)
        """
        with self.driver.session() as session:
            session.run(
                query,
                topic=topic,
                event_store_id=event_store_id,
                dq_result_id=str(dq_result_id),
                event_id=normalized.get("event_id"),
                event_type=normalized.get("event_type"),
                schema_id=normalized.get("schema_id"),
                schema_version=normalized.get("schema_version"),
                source_system=normalized.get("source_system"),
                correlation_id=normalized.get("correlation_id"),
                **dq,
                catalog_reference_key=resolution.get("catalog_reference_key"),
                matched_node_id=resolution.get("matched_node_id"),
                matched_label=resolution.get("matched_label"),
                match_method=resolution.get("match_method"),
                confidence=resolution.get("confidence"),
                match_status=resolution.get("status"),
            ).consume()

    def write_dlq_event(self, *, topic: str, dlq_id: int, payload: dict, error_type: str, error_message: str) -> None:
        query = """
        MERGE (d:DLQEvent {dlq_id: $dlq_id})
        SET d.topic = $topic,
            d.error_type = $error_type,
            d.error_message = $error_message,
            d.payload_preview = $payload_preview,
            d.received_at = datetime()
        MERGE (t:Topic {name: $topic})
        MERGE (d)-[:ARRIVED_ON]->(t)
        """
        with self.driver.session() as session:
            session.run(
                query,
                dlq_id=str(dlq_id),
                topic=topic,
                error_type=error_type,
                error_message=error_message,
                payload_preview=str(payload)[:1000],
            ).consume()
