# OLD BACKEND + DQC GraphRAG Agent Add-on

This package is ADDITIVE. It is designed for the old backend tree you shared.
It does not overwrite the legacy `app/dqc/` eventing pipeline or the old lineage/Marquez files.

## Copy strategy

Copy the contents of this package into the root of your old backend.

Safe new folders/files added:

```text
app/common/
app/catalog/
app/dqc/resolution/
app/embeddings/
app/graphrag/
app/agent/
app/observability/
scripts/catalog/
scripts/embeddings/
scripts/dqc_resolution/
migrations/sql/001_dqc_resolution_graphrag_agent.sql
requirements.additions.txt
.env.agent_and_resolution.additions.example
```

## Existing files to change manually

Only these old files need manual edits:

### 1) app/main.py

Keep all old imports/routers. Add these imports:

```python
# === DQC GraphRAG Agent add-on routers ===
from app.dqc.resolution.routes import router as dqc_resolution_router
from app.embeddings.routes import router as embeddings_router
from app.agent.routes import router as dqc_agent_router
from app.observability.routes import router as observability_router
```

Then add these includes after your old routers:

```python
# === DQC GraphRAG Agent add-on routers ===
app.include_router(dqc_resolution_router)
app.include_router(embeddings_router)
app.include_router(dqc_agent_router)
app.include_router(observability_router)
```

Do not remove old routers such as `app.routes`, `app.dqc.routes`, or `app.eventing.routes`.

### 2) requirements.txt

Append the content of:

```text
requirements.additions.txt
```

If a dependency already exists, keep one copy.

### 3) .env / .env.example

Append the variables from:

```text
.env.agent_and_resolution.additions.example
```

At minimum, configure your Azure LLM/embedding variables and database URL.

## Database migration

Run:

```bash
psql -d DataGalaxy_tables -f migrations/sql/001_dqc_resolution_graphrag_agent.sql
```

This creates additive tables such as:

```text
catalog_path_index
catalog_node_embeddings
dqc_raw
dqc_normalized
dqc_match_candidate
dqc_resolved
dqc_dlq
pipeline_logs
human_review_decisions
```

It should not drop or modify your old catalog/eventing tables.

## Run order

```bash
# 1. Build path index from old catalog tables
python scripts/catalog/build_catalog_path_index.py

# 2. Precompute catalog embeddings; these are stored and reused
python scripts/embeddings/generate_catalog_embeddings.py --limit 10000

# 3. Process existing DQC table
DQC_TABLE=DQC LIMIT=1000 python scripts/dqc_resolution/process_existing_dqc_table.py

# 4. Run backend
uvicorn app.main:app --reload
```

## New routes

```text
POST /dqc-resolution/upload
POST /dqc-resolution/connect/database
POST /dqc-resolution/process/event
GET  /dqc-resolution/resolved
GET  /dqc-resolution/unresolved
POST /dqc-resolution/review/{resolved_id}/approve
POST /dqc-resolution/review/{resolved_id}/reject

POST /embeddings/catalog/generate
POST /agent/dqc/run-workflow
POST /agent/dqc/chat
GET  /observability/logs
```

## What remains old and intact

These remain your old backend responsibilities:

```text
app/routes.py
app/services.py
app/repositories.py
app/marquez_client.py
app/openlineage_mapper.py
app/eventing/
app/dqc/ legacy eventing files
infra/logstash/
scripts/create_dqc_topics.py
scripts/run_dqc_consumer.py
scripts/replay_dqc_dlq.py
```

The new intelligent resolver lives under:

```text
app/dqc/resolution/
```

This separation is intentional.
