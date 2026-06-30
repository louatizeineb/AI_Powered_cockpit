from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, Integer, JSON, Text, func

# Your existing backend/app/db.py must expose Base.
from backend.app.db import Base


class DQCEventStore(Base):
    __tablename__ = "dqc_event_store"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    event_family = Column(Text, nullable=False, default="data_quality")
    event_type = Column(Text, nullable=False)
    schema_id = Column(Text, nullable=True)
    schema_version = Column(Text, nullable=True)
    source_system = Column(Text, nullable=True)
    correlation_id = Column(Text, nullable=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    payload_json = Column(JSON, nullable=False)
    status = Column(Text, nullable=False, default="VALID")


class DQCDLQ(Base):
    __tablename__ = "dqc_event_dlq"

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


class DQCResult(Base):
    __tablename__ = "dqc_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    id_ref = Column(Text, nullable=True)
    application_code = Column(Text, nullable=False)
    controlled_object_name = Column(Text, nullable=False)
    controlled_object_type = Column(Text, nullable=False)
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


class DQCCatalogResolution(Base):
    __tablename__ = "dqc_catalog_resolution"

    id = Column(Integer, primary_key=True, autoincrement=True)
    environment = Column(Text, nullable=False, default="test")
    event_store_id = Column(Integer, nullable=False)
    dq_result_id = Column(Integer, nullable=True)
    matched_node_id = Column(Text, nullable=True)
    matched_label = Column(Text, nullable=True)
    catalog_reference_key = Column(Text, nullable=True)
    match_method = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
