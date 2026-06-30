from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TableNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_key: str
    table_name: str
    canonical_table_name: str
    name_variants: list[str] = Field(default_factory=list)
    description: str
    source_system: str
    object_type: str | None = None
    relationship_table: bool = False
    required_columns: list[str] = Field(default_factory=list)
    export_ids: list[str] = Field(default_factory=list)
    contract_versions: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    file_hashes: list[str] = Field(default_factory=list)
    observed_column_count: int = 0
    expected_column_count: int = 0
    first_seen_export: str
    last_seen_export: str

    def properties(self) -> dict:
        return self.model_dump(mode="json")


class ColumnNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_key: str
    table_key: str
    column_name: str
    canonical_column_name: str
    raw_column_name: str
    name_variants: list[str] = Field(default_factory=list)
    description: str
    description_source: str
    observed_types: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sample_values: list[str] = Field(default_factory=list)
    export_ids: list[str] = Field(default_factory=list)
    contract_versions: list[str] = Field(default_factory=list)
    source_system: str
    mapping_decision: str
    mapping_confidence: float
    requires_human_approval: bool
    required_by_contract: bool
    present_in_latest_export: bool
    nullable_in_latest_export: bool | None = None
    null_count: int | None = None
    non_null_count: int | None = None
    distinct_count: int | None = None
    first_seen_export: str
    last_seen_export: str

    def properties(self) -> dict:
        payload = self.model_dump(mode="json")
        payload.pop("table_key")
        return payload


class SchemaProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: str
    contract_version: str
    source_system: str
    tables: list[TableNode]
    columns: list[ColumnNode]

    @property
    def relationship_count(self) -> int:
        return len(self.columns)
