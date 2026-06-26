# Governance Workspace Tutorial

This tutorial walks through the **Governance** workspace in the Athena Data Intelligence cockpit. The workspace is the operator view for `migration_v2`: it helps you review a migrated metadata export, resolve publish blockers, validate the trusted graph candidate, benchmark search readiness, and publish only after the release gates are ready.

The important idea is simple: agents and scripts can collect evidence and propose decisions, but the trusted graph changes only through explicit governed actions.

## 1. What The Workspace Is For

Use Governance when you have a DataGalaxy/Athena metadata export that has entered the `migration_v2` pipeline and you need to answer:

- Is this export ready to become the active trusted metadata graph?
- Which objects or relationships are trusted, quarantined, blocked, or waiting for review?
- What evidence supports each decision?
- Are the search index and graph version ready for activation?
- Which agent runs, tools, approvals, and reports produced the current state?

In the UI, Governance is the third top-level product area:

```text
Quality | Lineage | Governance
```

Click **Governance** in the product bar to open the Release Governance workspace.

## 2. Local Startup

Start the shared services:

```powershell
docker compose -f infra/docker-compose.yml up -d
```

Start the isolated migration graph if you are building or validating migration candidate graphs:

```powershell
docker compose -f infra/docker-compose.migration-v2.yml up -d
```

Start the Schema Intelligence graph if you are using the schema tab with Neo4j-backed schema projections:

```powershell
docker compose -f infra/docker-compose.schema-intelligence.yml up -d
```

Start the backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload --app-dir backend
```

Start the frontend:

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1 --port 5176
```

Open:

```text
http://127.0.0.1:5176
```

The frontend calls the backend through `VITE_API_BASE_URL`, which defaults to:

```text
http://127.0.0.1:8001
```

## 3. Required Data Before The UI Is Useful

The Governance workspace reads from the `migration_v2` control tables. If the export list is empty, the UI has no release to govern yet.

At minimum, you need:

- a registered export in `migration_export_run`;
- a workflow run in `migration_workflow_run`;
- validation queue rows in `migration_validation_queue`;
- publication snapshots in `migration_publication_snapshot` for release readiness;
- optional benchmark and publish reports under `reports/migration_v2/<export_id>/`.

For the bundled demo export, many docs and reports use:

```text
dg_old_athena_test
```

If you need to prepare a new export from scripts, the existing project flow is:

```powershell
.\.venv\Scripts\python.exe scripts\migration_v2\01_register_export.py --export-id <export_id> --export-path <raw_export_path> --contract backend\app\migration_v2\contracts\datagalaxy_athena_v1.yaml
.\.venv\Scripts\python.exe scripts\migration_v2\02_profile_export.py --export-id <export_id>
.\.venv\Scripts\python.exe scripts\migration_v2\03_detect_schema_drift.py --export-id <export_id> --contract backend\app\migration_v2\contracts\datagalaxy_athena_v1.yaml
.\.venv\Scripts\python.exe scripts\migration_v2\05_preprocess_to_staging.py --export-id <export_id> --contract backend\app\migration_v2\contracts\datagalaxy_athena_v1.yaml
.\.venv\Scripts\python.exe scripts\migration_v2\06_validate_staging.py --export-id <export_id>
.\.venv\Scripts\python.exe scripts\migration_v2\07_build_graph.py --export-id <export_id> --env-config configs\migration_v2\local_env.yaml
.\.venv\Scripts\python.exe scripts\migration_v2\16_populate_validation_queue.py --export-id <export_id> --env-config configs\migration_v2\local_env.yaml
```

The UI also exposes governed actions that call allowlisted backend tools, but it expects the export and workflow foundation to already exist.

## 4. The Governance Layout

The left sidebar has two important controls:

- **Active export**: choose which `export_id` you are governing.
- **Refresh evidence**: reload overview, queue, activity, and schema evidence from the backend.

The sidebar tabs are:

- **Release overview**
- **Schema**
- **Decision inbox**
- **Candidate graph**
- **Search readiness**
- **Release**
- **Agents & evidence**

Treat these tabs as a release journey. Start at the top, work down, and only publish when the Release tab is ready.

## 5. Release Overview

Use **Release overview** as the first stop.

What to look at:

- **Release decision**: tells you whether the candidate is ready or blocked.
- **Trusted objects** and **trusted relationships**: the graph slice eligible for normal search and traversal.
- **Quarantined objects**: retained as evidence, but excluded from default trusted traversal.
- **Hard blockers**: issues that prevent publish.
- **Workflow**: current phase, run ID, update time, and contract version.
- **Decision queue**: approved, pending, and resolved issue counts.

How to use it:

1. Select the export from **Active export**.
2. Read the release decision banner.
3. If blockers exist, click **Open decision inbox**.
4. If no blockers exist, click **Review release**.

If a blocker row is visible, click it to open the evidence inspector. The inspector shows the raw record behind the decision context.

## 6. Schema

Use **Schema** to understand the raw export shape and mapping proposals before trusting the graph.

The screen has three areas:

- **Schema Intelligence KG**: raw tables discovered for the export.
- **Table/Column graph view**: selected table and profiled columns.
- **Mapping proposals**: proposed schema actions, confidence, status, and human questions.

How to use it:

1. Pick a table from the left list.
2. Review the column nodes and profile hints.
3. Scan mapping proposals for low confidence, missing evidence, or pending human questions.
4. Click a proposal to inspect its evidence.

Good signs:

- expected raw tables are present;
- column counts match the export;
- mapping proposals are approved or intentionally unresolved;
- unexpected schema drift is documented before continuing.

Bad signs:

- important tables are missing;
- required contract columns are absent with no rationale;
- many proposals have low confidence or unanswered human questions.

## 7. Decision Inbox

Use **Decision inbox** to resolve validation queue items. This is the main governance control surface.

The queue combines deterministic findings with the latest agent proposal for each issue.

Each row shows:

- severity;
- issue type;
- identity, such as node ID or relationship endpoints;
- current publish policy;
- queue status;
- agent proposal;
- agent confidence;
- available actions.

Supported reviewer actions in the UI:

- **Accept**: keep the item in the trusted publication surface.
- **Quarantine**: keep it traceable, but exclude it from trusted search/traversal.
- **Repair**: mark it as requiring a technical correction before trust.

How to review an item:

1. Set **Reviewer** to your name or team identifier.
2. Write a clear **Decision rationale**.
3. Click a queue row to inspect evidence.
4. Compare policy, rationale, and agent proposal.
5. Choose **Accept**, **Quarantine**, or **Repair**.
6. Click **Refresh policy** after decisions so publication snapshots and blockers are recalculated.

Use conservative decisions:

- Accept only when the evidence proves the item belongs in the trusted graph.
- Quarantine when the item is useful for traceability but not reliable enough for default traversal.
- Repair when the issue is structural and needs an exact fix.

## 8. Candidate Graph

Use **Candidate graph** after the queue is mostly resolved. This tab checks the trusted graph projection before release.

The screen shows:

- trusted candidate object and relationship counts;
- invalid endpoint count;
- active graph version;
- quarantine projection preview.

Important actions:

- **Validate candidate projection** runs a candidate graph dry run.
- **Enforce trusted-only candidate** rebuilds/enforces the trusted projection so quarantined items stay outside the default trusted graph.

How to use it:

1. Confirm trusted counts are plausible.
2. Review quarantined examples to make sure policy is behaving as expected.
3. Run **Validate candidate projection**.
4. Run **Enforce trusted-only candidate** when the trusted/quarantine split looks correct.

Do not continue to publish if invalid endpoints or structural blockers remain.

## 9. Search Readiness

Use **Search readiness** to prove the candidate graph can support fast search before activation.

The acceptance targets shown in the UI are:

- cold p95 under 1 second;
- cached p95 under 150 ms;
- equivalent response shapes.

Important actions:

- **Refresh candidate index** activates or refreshes the isolated candidate search index.
- **Run benchmark** executes the benchmark against the candidate search endpoint.

How to use it:

1. Click **Refresh candidate index**.
2. Click **Run benchmark**.
3. Review cold p95, warm p95, graph version, and document count.
4. Inspect failed benchmark cases by clicking their rows.

Good signs:

- benchmark status is ready or passed;
- graph version is non-zero;
- document count is non-zero;
- warm and cold latency meet the displayed targets.

## 10. Release

Use **Release** only after the previous tabs look healthy.

The screen has:

- **Publish readiness**: remaining blockers from the publish report or publication snapshot.
- **Controlled activation**: approver and publish actions.

Safe release sequence:

1. Review any listed blockers.
2. Run **Run publish dry-run**.
3. Confirm there are no publish blockers.
4. Set **Approver** to the approving person or team.
5. Click **Publish trusted graph**.
6. Confirm the browser prompt.

The backend still enforces gates. The button can be visible, but publish will fail if the backend finds unresolved blockers.

## 11. Agents & Evidence

Use **Agents & evidence** when you need auditability or an explanation.

The tab includes:

- **Agent runs**: agent name, mode, status, proposal count, and timestamps.
- **Approval interrupts**: human gates raised by the workflow.
- **Allowlisted tool executions**: tool name, agent, status, version, input hash, artifacts, and timing.
- **Governance GraphRAG**: question box for evidence-backed explanations.

Example questions:

```text
Why is publication blocked, and what evidence should be reviewed next?
```

```text
Why was this node quarantined?
```

```text
What changed since the last policy refresh?
```

When an answer returns citations, click them to inspect the underlying event or subject.

## 12. Common Problems

### No exports appear

The backend is reachable, but `migration_export_run` has no rows or the migration PostgreSQL URL is wrong.

Check:

- `MIGRATION_V2_POSTGRES_URL` in `backend/.env`;
- `configs/migration_v2/local_env.yaml`;
- whether the export registration script has run.

### Backend says Migration V2 PostgreSQL is not configured

Set `MIGRATION_V2_POSTGRES_URL` or make sure `MIGRATION_V2_ENV_CONFIG_PATH` points to a file with `v2.postgres_url`.

### Governance actions fail with "Start a workflow before running governance actions"

The export exists, but no `migration_workflow_run` exists for it. Start or create the workflow before using UI actions.

Relevant API:

```text
POST /migration-v2/workflows/{export_id}/start
```

### Candidate search says refresh the isolated candidate search index

The search read model has no active candidate graph version. Use **Refresh candidate index** in Search readiness, then run the benchmark again.

### Publish stays blocked after decisions

Queue decisions do not automatically refresh every derived publication snapshot. Use **Refresh policy**, then refresh the evidence. If blockers remain, inspect the Decision inbox and Publish tabs again.

## 13. Backend Endpoints Used By Governance

The frontend uses these routes:

```text
GET  /migration-v2/exports
GET  /migration-v2/exports/{export_id}/overview
GET  /migration-v2/exports/{export_id}/validation-queue
POST /migration-v2/exports/{export_id}/validation-queue/{issue_id}/decision
GET  /migration-v2/exports/{export_id}/activity
GET  /migration-v2/exports/{export_id}/schema-intelligence
GET  /migration-v2/exports/{export_id}/schema-intelligence/{table_name}/columns
GET  /migration-v2/exports/{export_id}/governance-items
POST /migration-v2/exports/{export_id}/actions/{action}
POST /graphrag/governance/retrieve
```

The UI action names map to backend allowlisted tools:

| UI action | Backend action | Purpose |
| --- | --- | --- |
| Refresh policy | `refresh-policy` | Rebuild conditional publication policy evidence. |
| Validate candidate projection | `candidate-dry-run` | Dry-run candidate graph validation. |
| Enforce trusted-only candidate | `enforce-trusted-graph` | Keep quarantine out of trusted projection. |
| Refresh candidate index | `activate-candidate-search` | Refresh candidate search read model. |
| Run benchmark | `benchmark` | Measure candidate search readiness. |
| Run publish dry-run | `publish-dry-run` | Test publish gates without activation. |
| Publish trusted graph | `publish` | Activate the trusted graph version after approval. |

## 14. Recommended Operator Routine

For a normal governance review:

1. Open **Governance** and select the export.
2. Start in **Release overview** and identify the next required action.
3. Review **Schema** if there are mapping or drift concerns.
4. Work **Decision inbox** until pending/blocking items are resolved or intentionally quarantined.
5. Click **Refresh policy**.
6. Validate **Candidate graph**.
7. Refresh and benchmark **Search readiness**.
8. Run **Release** dry-run.
9. Publish only when the dry-run and publish readiness are clean.
10. Use **Agents & evidence** whenever you need to justify a decision or audit the path to release.

The workflow is designed to keep uncertain metadata visible but bounded. Trusted items can ship; quarantined items remain explainable; unresolved hard blockers keep publication honest.
