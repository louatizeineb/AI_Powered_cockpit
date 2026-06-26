# Migration V2 Operational Plan

This document describes the proposed `migration_v2` framework for DataGalaxy / Athena exports. The goal is to make catalog and lineage migration more intelligent, resilient, explainable, auditable, and benchmarkable while preserving the current PostgreSQL to Neo4j importer as baseline `v0`.

`migration_v2` must live next to the existing migration. It should not replace [import_postgres_metadata_lineage_to_neo4j.py](../../scripts/import_postgres_metadata_lineage_to_neo4j.py) until it repeatedly proves equal or better graph quality, lineage coverage, and operational safety against real exports.

## 1. Current Baseline Migration Summary

The current baseline migration is centered on [import_postgres_metadata_lineage_to_neo4j.py](../../scripts/import_postgres_metadata_lineage_to_neo4j.py).

The baseline flow is:

1. Raw DataGalaxy / Athena exports are converted by preprocessing scripts into PostgreSQL catalog tables.
2. The importer reads `source`, `container`, `structure`, `field`, optional `usage`, and `link` tables.
3. It uses `node_id` as the stable catalog identifier. That value is derived from DataGalaxy `v_tech_ident_entt`.
4. It creates Neo4j catalog labels:
   - `Source`
   - `Container`
   - `Structure`
   - `Field`
   - `Usage`
   - `BusinessTerm`
   - `DataProcessing`
   - `DataProcessingItem`
   - `DataGalaxyObject`
5. It creates catalog hierarchy relationships:
   - `Container` and `Structure` through `CONTAINS`
   - `Field` through `HAS_FIELD`
6. It creates lineage relationships from `link` rows by mapping `link_type` values such as `Implements`, `Uses`, `IsInputOf`, and `IsOutputOf` to Neo4j relationship types.
7. It creates canonical `Field -> BusinessTerm` `IMPLEMENTS` relationships.
8. It creates optional `Usage -> Source` and `Usage -> Structure` relationships through `app_code` and `dataset_ref`.
9. It prints Neo4j counts and unresolved parent summaries after loading.
10. It refreshes the PostgreSQL search read model through `refresh_lineage_search_documents()` when that function is installed.

This baseline is valuable because it already captures the core semantic shape of the cockpit:

- DataGalaxy hierarchy.
- Business term implementation links.
- Usage context.
- Neo4j traversal.
- PostgreSQL search read model.
- Redis graph-version cache integration through the existing search architecture.

## 2. Problems With The Current Model

The current migration is useful, but it is brittle when new raw exports change shape or quality.

Key limitations:

- Raw export schema drift is discovered late, usually during preprocessing, graph load, or visual inspection.
- Mapping logic is distributed across scripts and implicit table conventions.
- There is no first-class export registry, so each run is hard to compare, replay, or audit.
- The migration assumes PostgreSQL catalog tables are already clean enough.
- Validation is mostly post-load and count-oriented.
- Proposed, Deprecated, and Obsolete objects are not clearly separated from the production graph policy.
- Raw columns that are not mapped are not systematically preserved for audit.
- Human approval is not explicit before mapping, repair, graph build, or publish.
- Baseline-vs-new comparison is not part of the workflow.
- Neo4j writes happen after limited staging evidence.
- The current flow is harder to adapt to a fresh DataGalaxy Athena export with renamed, missing, or newly added columns.

The most important known data rule is:

```text
parent.v_tech_ident_entt = child.v_drct_prnt_entt_ident
```

The most important warning is:

```text
v_ident_works is the workspace UUID. It is constant across the export and must never be used as an entity join key.
```

## 3. New Migration V2 Architecture

`migration_v2` introduces a controlled migration pipeline around deterministic scripts and auditable staging.

The proposed high-level flow is:

1. Detect or register a new raw export.
2. Profile raw CSV files and store column-level evidence.
3. Detect schema drift against a versioned mapping contract.
4. Generate or validate a mapping plan.
5. Require human approval for mapping and drift decisions.
6. Preprocess raw rows into canonical PostgreSQL staging tables.
7. Validate staging before any graph write.
8. Require human approval for warnings, repairs, and exclusions.
9. Build a new Neo4j graph version from staging.
10. Generate lineage path read models.
11. Audit graph quality and compare against baseline `v0`.
12. Require human publish approval.
13. Publish the active graph version and refresh the search read model.

The target folder structure is:

```text
backend/app/migration_v2/
  __init__.py
  contracts/
    datagalaxy_athena_v1.yaml
  models/
    migration_models.py
    staging_models.py
  profiling/
    raw_export_profiler.py
    schema_drift_detector.py
  mapping/
    contract_loader.py
    mapping_engine.py
    canonicalizer.py
  preprocessing/
    cleaners.py
    normalizers.py
    type_parsers.py
    path_utils.py
  validation/
    validators.py
    validation_rules.py
    finding_repository.py
  graph/
    neo4j_schema.py
    graph_builder.py
    lineage_path_builder.py
    graph_auditor.py
  agents/
    export_detection_agent.py
    schema_profiling_agent.py
    documentation_agent.py
    mapping_agent.py
    preprocessing_agent.py
    validation_guardian_agent.py
    graph_builder_agent.py
    lineage_path_agent.py
    audit_agent.py
    report_agent.py
  orchestration/
    migration_orchestrator.py
    human_gate_service.py
    benchmark_service.py
  reports/
    report_writer.py
```

Command entry points should live under:

```text
scripts/migration_v2/
```

### Control Plane And Data Plane

The framework separates orchestration from execution:

- Agents inspect, explain, propose mappings, summarize findings, and ask for approval.
- Deterministic scripts perform profiling, preprocessing, validation, PostgreSQL writes, Neo4j writes, and report generation.
- No LLM should directly mutate PostgreSQL or Neo4j without evidence, validation output, and a human gate when risk is material.

## 4. Multi-Agent Intervention Points

Agents should add intelligence around deterministic steps, not replace deterministic steps.

| Agent | Mission | Tools / scripts | Output | Human approval |
| --- | --- | --- | --- | --- |
| `ExportDetectionAgent` | Detect or register new raw exports and infer export identity. | `01_register_export.py`, file inspection. | Export run metadata. | Required when export identity is ambiguous. |
| `SchemaProfilingAgent` | Explain raw file shape, column completeness, uniqueness, nulls, and likely keys. | `02_profile_export.py`, profiler module. | Column profile report and schema summary. | Not usually required. |
| `DocumentationAgent` | Produce migration notes, mapping explanations, and run summaries. | Report writer, docs templates. | Markdown reports. | Not usually required. |
| `MappingAgent` | Compare raw schema to the contract and propose mapping decisions. | `03_detect_schema_drift.py`, contract loader, mapping engine. | Mapping plan, drift report, unresolved decisions. | Required for drift, renamed columns, or ambiguous matches. |
| `PreprocessingAgent` | Coordinate canonical staging generation and explain dropped or normalized values. | `05_preprocess_to_staging.py`, cleaners, normalizers, type parsers. | Staging load report. | Required if repairs or exclusions are suggested. |
| `ValidationGuardianAgent` | Block unsafe graph writes when staging quality fails policy. | `06_validate_staging.py`, validation rules, finding repository. | Validation findings and gate recommendation. | Required for warnings, repairs, or threshold overrides. |
| `GraphBuilderAgent` | Build or request build of candidate Neo4j graph version from approved staging. | `07_build_graph.py`, graph builder, Neo4j schema module. | Candidate graph version and build report. | Required before production publish. |
| `LineagePathAgent` | Generate path read model for common lineage queries and explain coverage. | `08_generate_lineage_paths.py`, lineage path builder. | `lineage_path` rows or `LineagePath` nodes. | Not usually required unless path coverage regresses. |
| `AuditAgent` | Audit graph completeness, orphan rates, relationship distribution, and search readiness. | `09_audit_and_compare.py`, graph auditor, benchmark service. | Audit report and baseline comparison. | Required for publish decision. |
| `ReportAgent` | Assemble an executive and technical migration packet. | Report writer, all JSON reports. | Final run report. | Required for publish packet sign-off. |

## 5. Human-In-The-Loop Gates

`migration_v2` should have explicit gates stored as durable decisions in `migration_mapping_decision` or a related gate table.

### Gate 1: Mapping And Drift Approval

Before preprocessing:

- Approve raw table detection.
- Approve schema drift classification.
- Approve mapping contract version.
- Approve any inferred column mapping.
- Reject any mapping that uses `v_ident_works` as an entity key.

### Gate 2: Staging Validation Approval

Before graph build:

- Review unresolved parents.
- Review duplicate `node_id` values.
- Review invalid hierarchy links.
- Review relationship endpoints missing from staging.
- Review status filtering impact.
- Approve repair rules or accept warnings.

### Gate 3: Publish Approval

Before making a graph version active:

- Review graph audit.
- Review lineage path coverage.
- Review baseline-vs-v2 comparison.
- Confirm search read model refresh.
- Confirm rollback path to baseline graph version.

## 6. Preprocessing Strategy

Preprocessing should transform raw DataGalaxy Athena files into canonical staging, while preserving enough raw evidence for replay and audit.

Core rules:

- Use a versioned mapping contract as the source of truth.
- Preserve unknown columns in a JSON payload when `preserve_unknown_columns` is true.
- Normalize identifiers by trimming whitespace and converting empty strings to null.
- Normalize boolean values from DataGalaxy-style values, database booleans, numeric flags, and common text values.
- Parse dates with explicit formats when possible, and store parse failures as validation findings.
- Keep all statuses in staging and in the production graph candidate; status is audit metadata, not a default graph filter.
- Treat `Proposed`, `Deprecated`, `Obsolete`, and other statuses as graph-visible audit metadata; user-facing filters can decide how to display or hide them later.
- Never join entities on `v_ident_works`.
- Apply the universal hierarchy rule from `v_drct_prnt_entt_ident` to parent `v_tech_ident_entt`.

Raw input tables expected in the first contract:

- `diso_dico_source`
- `dict_dico_container`
- `dist_dico_structure`
- `difi_dico_field`
- `lien_link_entt`

Optional raw inputs:

- Usage exports.
- Future event or DQC pipeline metadata.

## 7. Canonical Staging Model

The canonical staging model should store object rows and relationship rows independent of raw export naming.

### `migration_export_run`

Tracks one logical export migration attempt.

Important fields:

- `export_id`
- `export_path`
- `contract_version`
- `status`
- `created_at`
- `started_at`
- `completed_at`
- `metadata`

### `migration_raw_file`

Tracks raw files discovered for an export.

Important fields:

- `export_id`
- `raw_table_name`
- `file_path`
- `file_hash`
- `row_count`
- `column_count`
- `detected_format`

### `migration_column_profile`

Stores profiling output per raw column.

Important fields:

- `export_id`
- `raw_table_name`
- `column_name`
- `data_type_guess`
- `null_count`
- `distinct_count`
- `sample_values`
- `warnings`

### `migration_mapping_decision`

Stores deterministic and human-approved mapping decisions.

Important fields:

- `export_id`
- `contract_version`
- `raw_table_name`
- `raw_column_name`
- `canonical_field`
- `decision_type`
- `confidence`
- `approved_by`
- `approved_at`
- `rationale`

### `catalog_object_staging`

Canonical object staging table for `Source`, `Container`, `Structure`, `Field`, `BusinessTerm`, `DataProcessing`, and related nodes.

Important fields:

- `export_id`
- `node_id`
- `parent_node_id`
- `object_type`
- `name_label`
- `name_tech`
- `path_full`
- `entity_type`
- `data_type`
- `status`
- `app_code`
- `source_table`
- `raw_payload`
- `unknown_columns`
- `is_graph_eligible`
- `graph_exclusion_reason`

### `catalog_relationship_staging`

Canonical relationship staging table.

Important fields:

- `export_id`
- `src_node_id`
- `tgt_node_id`
- `relationship_type`
- `relationship_family`
- `source_table`
- `link_type`
- `status`
- `raw_payload`
- `is_graph_eligible`
- `graph_exclusion_reason`

### `migration_validation_finding`

Durable validation findings.

Important fields:

- `export_id`
- `severity`
- `category`
- `entity_type`
- `node_id`
- `relationship_id`
- `message`
- `evidence`
- `status`

### `lineage_path`

Read model for precomputed or materialized lineage paths.

Important fields:

- `export_id`
- `graph_version`
- `start_node_id`
- `end_node_id`
- `path_hash`
- `path_nodes`
- `path_relationships`
- `path_length`
- `path_family`

### `migration_benchmark_result`

Stores baseline-vs-v2 comparison metrics.

Important fields:

- `export_id`
- `baseline_name`
- `metric_name`
- `baseline_value`
- `v2_value`
- `delta_value`
- `delta_pct`
- `status`
- `evidence`

## 8. Neo4j Graph Model

The v2 graph model should preserve the baseline labels and relationship types so the existing frontend and search layer remain stable.

### Node Labels

Baseline-compatible labels:

- `DataGalaxyObject`
- `Source`
- `Container`
- `Structure`
- `Field`
- `Usage`
- `BusinessTerm`
- `DataProcessing`
- `DataProcessingItem`

Additional v2 metadata labels may be added only if they do not break existing queries:

- `GraphVersioned`
- `MigrationV2`
- `LineagePath`

### Relationships

Baseline-compatible relationships:

- `CONTAINS`
- `HAS_FIELD`
- `IMPLEMENTS`
- `USES`
- `IS_INPUT_OF`
- `IS_OUTPUT_OF`
- Other mapped `link_type` relationships.

V2 relationship properties should include:

- `export_id`
- `graph_version`
- `source_table`
- `contract_version`
- `mapping_decision_id`
- `validation_status`

### Graph Versioning

The preferred publish model is:

1. Build a candidate graph version from approved staging.
2. Audit candidate graph version.
3. Publish by updating active graph version metadata.
4. Refresh PostgreSQL search read model.
5. Invalidate Redis by graph version change.

The old importer remains the baseline path while v2 matures.

## 9. Lineage Path Read Model

The lineage path read model should make common lineage exploration faster and more explainable.

It can be represented as:

- PostgreSQL `lineage_path` rows for search, filtering, and reports.
- Optional Neo4j `LineagePath` nodes for graph-native path summaries.

Path generation should include:

- `Field -> BusinessTerm` semantic paths.
- `Source -> Container -> Structure -> Field` hierarchy paths.
- Upstream and downstream technical lineage paths from `link` relationships.
- Usage-to-catalog paths where usage export exists.

Each path should store:

- Start and end node IDs.
- Ordered node IDs.
- Ordered relationship types.
- Path length.
- Path family.
- Path hash.
- Evidence source.
- Graph version.

## 10. Baseline Vs V2 Benchmark Strategy

The old migration should be used as benchmark `v0`.

Recommended benchmark dimensions:

- PostgreSQL source row counts by catalog table.
- Staging object counts by object type and status.
- Neo4j node counts by label.
- Neo4j relationship counts by type.
- `Field -> BusinessTerm` `IMPLEMENTS` count.
- Hierarchy orphan count.
- Missing relationship endpoint count.
- Duplicate `node_id` count.
- Search document count.
- Lineage path count.
- Graph build duration.
- Search refresh duration.
- Warnings and errors by category.

Benchmark outputs:

- `reports/migration_v2/{export_id}/baseline_report.json`
- `reports/migration_v2/{export_id}/audit_report.json`
- `reports/migration_v2/{export_id}/benchmark_report.md`
- Rows in `migration_benchmark_result`

Publish should require a comparison result. A candidate graph can be published when differences are explained and approved, not necessarily when all counts match exactly.

## 11. Step-By-Step Implementation Roadmap

### Phase 0: Preserve Baseline

- Keep [import_postgres_metadata_lineage_to_neo4j.py](../../scripts/import_postgres_metadata_lineage_to_neo4j.py) untouched.
- Add `00_run_baseline.py` to execute or measure baseline runs.
- Store baseline reports under `reports/migration_v2/{export_id}/`.

### Phase 1: Contracts And Staging Schema

- Add `backend/app/migration_v2/contracts/datagalaxy_athena_v1.yaml`.
- Add SQL migration `010_migration_v2_staging.sql`.
- Add SQLAlchemy models for export runs, raw files, profiles, staging objects, staging relationships, findings, lineage paths, and benchmark results.

### Phase 2: Export Registry And Profiling

- Add `01_register_export.py`.
- Add `02_profile_export.py`.
- Profile file presence, row counts, columns, nulls, distinct counts, samples, and hash values.

### Phase 3: Schema Drift And Mapping

- Add `03_detect_schema_drift.py`.
- Add contract loader and mapping engine.
- Generate drift reports with required, optional, unknown, missing, and suspicious columns.
- Add mapping decisions with explicit human approval state.

### Phase 4: Canonical Preprocessing

- Add `05_preprocess_to_staging.py`.
- Convert raw tables into `catalog_object_staging` and `catalog_relationship_staging`.
- Preserve unknown columns and raw payload.
- Mark graph eligibility by status policy.

### Phase 5: Validation Guardian

- Add `06_validate_staging.py`.
- Validate duplicate IDs, forbidden joins, parent resolution, relationship endpoint resolution, status distribution, and required fields.
- Store findings and generate gate recommendation.

### Phase 6: Candidate Graph Build

- Add `07_build_graph.py`.
- Create constraints and indexes for v2 graph properties.
- Load only approved and graph-eligible staging rows.
- Preserve baseline labels and relationship names.

### Phase 7: Lineage Path Read Model

- Add `08_generate_lineage_paths.py`.
- Generate path rows from staged graph or Neo4j candidate graph.
- Store path hashes for stable comparison.

### Phase 8: Audit, Benchmark, Publish

- Add `09_audit_and_compare.py`.
- Compare v2 against baseline.
- Add `10_publish_graph_version.py`.
- Refresh search read model only after human publish approval.

## 12. Commands To Run Each Phase

Apply the staging schema first:

```powershell
python scripts/migration_v2/apply_staging_schema.py
```

Start an isolated Neo4j sandbox for migration_v2 graph tests:

```powershell
$env:MIGRATION_V2_NEO4J_PASSWORD="change_me"
docker compose -f infra/docker-compose.migration-v2.yml up -d
$env:NEO4J_URI="bolt://127.0.0.1:7689"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD=$env:MIGRATION_V2_NEO4J_PASSWORD
```

The migration_v2 Neo4j browser is `http://localhost:7476`. The existing catalog Neo4j remains on `http://localhost:7474` and `bolt://127.0.0.1:7687`.

```powershell
python scripts/migration_v2/00_run_baseline.py --export-id dg_2026_new
python scripts/migration_v2/01_register_export.py --export-path data/raw/datagalaxy/export_2026_new --export-id dg_2026_new
python scripts/migration_v2/02_profile_export.py --export-id dg_2026_new
python scripts/migration_v2/03_detect_schema_drift.py --export-id dg_2026_new --contract backend/app/migration_v2/contracts/datagalaxy_athena_v1.yaml
python scripts/migration_v2/05_preprocess_to_staging.py --export-id dg_2026_new --contract backend/app/migration_v2/contracts/datagalaxy_athena_v1.yaml
python scripts/migration_v2/06_validate_staging.py --export-id dg_2026_new
python scripts/migration_v2/07_build_graph.py --export-id dg_2026_new --env-config configs/migration_v2/local_env.yaml
python scripts/migration_v2/08_generate_lineage_paths.py --export-id dg_2026_new --env-config configs/migration_v2/local_env.yaml
python scripts/migration_v2/09_audit_and_compare.py --export-id dg_2026_new --env-config configs/migration_v2/local_env.yaml
python scripts/migration_v2/11_agent_gate_review.py --export-id dg_2026_new
# Do not run publish until graph audit, lineage paths, benchmark, and human approval are complete:
# python scripts/migration_v2/10_publish_graph_version.py --export-id dg_2026_new
```

The first v2 query endpoints should remain read-only until publish is approved:

```text
GET /lineage/explorer/node/{id}/paths
GET /lineage/explorer/node/{id}/audit-context
```

Recommended first-run order:

1. Run baseline and save evidence.
2. Register export.
3. Profile raw files.
4. Detect drift.
5. Review mapping/drift report.
6. Approve mapping gate.
7. Preprocess to staging.
8. Validate staging.
9. Review validation findings.
10. Approve graph-build gate.
11. Build candidate graph.
12. Generate lineage paths.
13. Audit and compare.
14. Approve publish gate.
15. Publish graph version and refresh search read model.

## 13. Risks And Mitigation Strategy

| Risk | Impact | Mitigation |
| --- | --- | --- |
| `v_ident_works` is accidentally used as an entity join key. | Catastrophic false joins because the value is constant across the export. | Contract-level forbidden join column, validation rule, and hard publish blocker. |
| New export renames or removes required columns. | Missing nodes or relationships. | Schema drift detection before preprocessing and human mapping gate. |
| Status semantics are misunderstood. | Users may confuse Proposed, Deprecated, or Obsolete metadata with validated metadata. | Preserve every status in the graph, keep status visible as metadata, and use audit/report filters instead of migration-time exclusion. |
| Baseline and v2 counts differ. | Loss of trust in v2. | Benchmark every run and require explanation before publish. |
| LLM proposes unsafe repairs. | Incorrect graph writes. | Agents propose only; deterministic scripts execute; human approval gates are durable. |
| Unknown columns contain useful future metadata. | Information loss. | Preserve unknown columns in JSON payloads. |
| Neo4j graph version is built but search model is stale. | Frontend search and graph traversal disagree. | Publish flow must include search refresh and active graph version update. |
| Large exports cause slow profiling or staging. | Operational delay. | Use Polars or pandas chunking according to local conventions, batch SQL writes, and persist intermediate reports. |
| Data quality issues are hidden inside logs. | Difficult audit and support. | Store validation findings in PostgreSQL and write Markdown/JSON reports per export. |

## Design Position

The leap from baseline to `migration_v2` is not a replacement of the previous work. It is an operational shell around it.

The old migration captured the essence of the cockpit: DataGalaxy identifiers, hierarchy, business lineage, Neo4j traversal, and PostgreSQL-backed search. `migration_v2` keeps that essence by preserving labels, relationship names, stable IDs, and the search publish flow. It adds the missing operational discipline: contracts, profiling, staging, validation, approvals, audit reports, and baseline comparison.

In practical terms, v2 should earn the right to publish. Until then, v0 remains the known baseline and v2 is a candidate graph factory with better evidence.
