# Migration V2 Agent Workflow

This document explains the publish-governance workflow for migrated enterprise metadata knowledge graphs.
The design goal is simple: agents can inspect, explain, propose, and route anomalies, but only explicit approval can change publish gates.

## End-to-End Flow

```mermaid
flowchart TD
    A["Old export / v0 graph"] --> B["Baseline Builder<br/>00_run_baseline.py"]
    B --> C["Contract + baseline reports"]
    C --> D["New DataGalaxy export arrives"]
    D --> E["ExportDetectionAgent<br/>01_register_export.py"]
    E --> F["SchemaProfilingAgent<br/>02_profile_export.py"]
    F --> G["MappingAgent<br/>03_detect_schema_drift.py<br/>04_generate_mapping_plan.py"]
    G --> H{"Contract drift?"}
    H -- "unknown / risky" --> H1["Human mapping approval"]
    H1 --> I["PreprocessingAgent<br/>05_preprocess_to_staging.py"]
    H -- "known / accepted" --> I
    I --> J["ValidationAgent<br/>06_validate_staging.py"]
    J --> K{"Open validation errors?"}
    K -- "yes" --> K1["Repair source mapping or staging"]
    K1 --> I
    K -- "no" --> L["GraphBuilderAgent<br/>07_build_graph.py<br/>08_generate_lineage_paths.py"]
    L --> M["AuditAgent<br/>09_audit_and_compare.py<br/>12_publish_hardening_audit.py"]
    M --> N["ValidationGuardianAgent<br/>16_populate_validation_queue.py<br/>18_run_validation_queue_agents.py"]
    N --> O["Agent proposals<br/>accept / quarantine / exclude / repair / needs_human / block"]
    O --> P{"Approved reviewer decision?"}
    P -- "no" --> P1["Manual review CSV<br/>09_validation_queue.csv"]
    P1 --> P
    P -- "yes" --> Q["DecisionApplier<br/>17_apply_validation_queue_decisions.py"]
    Q --> R["Queue report regenerated"]
    R --> S["FastSearchBenchmarkAgent<br/>13_benchmark_fast_search.py"]
    S --> T{"Publish gates ready?"}
    T -- "no" --> T1["Remaining blockers stay in queue"]
    T1 --> N
    T -- "yes" --> U["PublishAgent<br/>10_publish_graph_version.py --approved-by"]
    U --> V["Active graph version + rollback metadata"]
```

## Agent Roles

| Agent | Job | Tools | Can mutate? | Human approval |
| --- | --- | --- | --- | --- |
| ExportDetectionAgent | Register raw files and detect the export boundary. | `01_register_export.py`, contract loader, file hashing | Yes, migration registry only | Not normally |
| SchemaProfilingAgent | Profile raw table columns, nulls, row counts, and shape drift. | `02_profile_export.py`, raw export profiler | Yes, report/staging metadata only | Required for unexpected drift |
| MappingAgent | Compare export schema to contract and propose mapping changes. | `03_detect_schema_drift.py`, `04_generate_mapping_plan.py`, mapping contract | No trusted graph mutation | Required for unknown columns or inferred mappings |
| PreprocessingAgent | Normalize raw rows into staging tables. | `05_preprocess_to_staging.py`, cleaners, normalizers, type parsers | Yes, staging only | Required if cleaning policy changes |
| ValidationAgent | Run deterministic staging checks. | `06_validate_staging.py`, validation rules | Yes, findings only | Required for open errors |
| GraphBuilderAgent | Build candidate typed graph from approved staging. | `07_build_graph.py`, graph builder, Neo4j schema | Yes, candidate graph only | Required before production publish |
| AuditAgent | Compare v2 graph with v0 baseline and generate anomaly reports. | `09_audit_and_compare.py`, `12_publish_hardening_audit.py`, graph auditor | Reports only | Required for parity exceptions |
| ValidationGuardianAgent | Review unresolved queue items with LLM or deterministic fallback and produce proposals. | `16_populate_validation_queue.py`, `18_run_validation_queue_agents.py`, Azure/OpenAI, validation queue | Proposals only | Always before decisions affect publish |
| DecisionApplier | Apply explicit reviewer-approved or explicitly authorized low-risk proposals. | `17_apply_validation_queue_decisions.py` | Yes, validation queue status only | Always requires `--approved-by` |
| FastSearchBenchmarkAgent | Verify read model, cache behavior, graph version headers, and latency. | `13_benchmark_fast_search.py`, API, Redis, Postgres search read model | Benchmark rows/reports only | Required before publish |
| PublishAgent | Publish only when all gates are ready. | `10_publish_graph_version.py`, search refresh function, publish reports | Yes, active search graph version | Always requires `--approved-by` |

## Current Governance Model

The validation queue is the control surface. Reports feed it, agents propose actions, and approval changes queue state.

Allowed queue policies:

- `accept`: keep the object or delta in the trusted graph, with documented rationale.
- `quarantine`: keep it traceable but do not treat it as trusted hierarchy/search evidence.
- `exclude`: remove from trusted publish surface by policy.
- `repair`: known technical issue that needs exact repair evidence.
- `needs_human`: not enough evidence for automatic decision.
- `block`: publish cannot proceed until resolved.

The agent is intentionally conservative:

- `HAS_FIELD -1` remains `repair` until the exact missing edge is known.
- `IMPLEMENTS -155` remains `needs_human` or `repair` until edge-level diff exists.
- High severity `accept` proposals are downgraded to `needs_human`.
- Placeholder/null paths are not silently accepted.

## Shared DQC Agent Governance

The DQC Resolution Agent follows the same production boundary as the migration agents:
it inspects evidence, explains match quality, persists proposals, and recommends reviewer actions, but chat does not approve or reject matches.

Allowed DQC proposal actions are:

- `approve_match`: reviewer may approve a high-confidence resolved match.
- `reject_match`: reviewer may reject unsafe match evidence.
- `keep_in_dlq`: unresolved event stays in DLQ.
- `search_alternatives`: reviewer should inspect other candidates or GraphRAG evidence.
- `replay_after_fix`: source data or catalog evidence must be corrected before replay.

DQC review decisions remain explicit API/UI actions under `/dqc-resolution/review/...`.
The DQC agent tool registry deliberately excludes approval and rejection tools.

## One-Command Agent Workflow

Use this when the queue already exists and you want the agent-assisted publish-readiness loop:

```powershell
.\.venv\Scripts\python.exe scripts\migration_v2\19_run_agent_publish_workflow.py `
  --export-id dg_old_athena_test `
  --env-config configs\migration_v2\local_env.yaml `
  --limit 200 `
  --apply-low-risk `
  --approved-by louat
```

Outputs:

- `reports/migration_v2/<export_id>/agent_publish_workflow_report.json`
- `reports/migration_v2/<export_id>/agent_publish_workflow_report.md`
- `reports/migration_v2/<export_id>/agent_validation_queue_proposals.json`
- `reports/migration_v2/<export_id>/manual_review_csv/10_agent_queue_proposals.csv`
- regenerated `validation_queue_report.json/md`
- regenerated `publish_report.json/md`

## When a New Export Arrives

1. Register it with the current contract.
2. Profile it and detect schema drift.
3. If drift is material, update or approve the contract before graph build.
4. Preprocess into staging.
5. Run deterministic staging validation.
6. Build candidate graph and lineage paths.
7. Audit against the old baseline and generate hardening reports.
8. Populate the validation queue.
9. Run ValidationGuardianAgent for evidence-backed proposals.
10. Apply only approved decisions or explicitly authorized low-risk proposals.
11. Run fast search benchmark.
12. Publish only when validation queue and fast search gates are both ready.

## Why This Is Not Hardcoded Migration Logic

The code does not bake in one-off answers like "always accept this node." It encodes governance policy:

- source evidence is stored per issue;
- agent proposals are persisted separately from queue decisions;
- approval is captured with approver and timestamp;
- queue rebuild preserves approved decisions;
- unresolved items remain visible and block publish according to policy;
- new exports re-enter the same queue/proposal/approval workflow.

That means a new export can bring new anomalies without forcing code changes. The system should classify what it can, propose what it cannot prove, and keep the unresolved cases in a validation queue instead of pretending the graph is perfect.
