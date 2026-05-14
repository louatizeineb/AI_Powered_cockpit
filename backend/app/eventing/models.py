from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, Integer, JSON, Text, func

# This expects your existing backend/app/db.py to expose Base.
# If your Base is elsewhere, change only this import.
from backend.app.db import Base


class EventStore(Base):
    __tablename__ = "event_store"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    event_family = Column(Text, nullable=False)
    event_type = Column(Text, nullable=False)
    schema_id = Column(Text, nullable=True)
    schema_version = Column(Text, nullable=True)
    source_system = Column(Text, nullable=True)
    correlation_id = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    payload_json = Column(JSON, nullable=False)
    status = Column(Text, nullable=False, default="VALID")


class EventDLQ(Base):
    __tablename__ = "event_dlq"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    topic = Column(Text, nullable=False)
    event_family = Column(Text, nullable=True)
    schema_id = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    payload_json = Column(JSON, nullable=False)
    error_type = Column(Text, nullable=False)
    error_message = Column(Text, nullable=False)
    replay_status = Column(Text, nullable=False, default="PENDING")


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    correlation_id = Column(Text, nullable=True)
    pipeline_type = Column(Text, nullable=True)
    event_status = Column(Text, nullable=True)
    event_type = Column(Text, nullable=True)
    pipeline_name = Column(Text, nullable=False)
    status = Column(Text, nullable=True)
    start_time = Column(Text, nullable=True)
    end_time = Column(Text, nullable=True)
    duration = Column(Text, nullable=True)
    source_database = Column(Text, nullable=True)
    source_table = Column(Text, nullable=True)
    environment_name = Column(Text, nullable=True)
    severity = Column(Text, nullable=True)
    raw_event_id = Column(Integer, nullable=True)


class DataQualityCheckResult(Base):
    __tablename__ = "data_quality_check_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    id_ref = Column(Text, nullable=True)
    application_code = Column(Text, nullable=False)
    controlled_object_name = Column(Text, nullable=True)
    controlled_object_type = Column(Text, nullable=True)
    controlled_source_name = Column(Text, nullable=True)
    business_term_name = Column(Text, nullable=True)
    control_name = Column(Text, nullable=False)
    quality_dimension = Column(Text, nullable=True)
    acceptance_threshold = Column(Float, nullable=True)
    execution_timestamp = Column(Text, nullable=False)
    business_date = Column(Text, nullable=False)
    controlled_item_count = Column(Integer, nullable=False)
    ok_count = Column(Integer, nullable=False)
    ko_count = Column(Integer, nullable=False)
    control_tool = Column(Text, nullable=False)
    computed_score = Column(Float, nullable=True)
    quality_status = Column(Text, nullable=True)
    raw_event_id = Column(Integer, nullable=True)


class EventCatalogResolution(Base):
    __tablename__ = "event_catalog_resolution"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    event_store_id = Column(Integer, nullable=False)
    event_family = Column(Text, nullable=False)
    matched_node_id = Column(Text, nullable=True)
    matched_label = Column(Text, nullable=True)
    catalog_reference_key = Column(Text, nullable=True)
    match_method = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(Text, nullable=False)
