# AI-Powered Data Quality Cockpit

Local development workspace for the DataGalaxy lineage explorer and data-quality cockpit.

## Structure

```text
backend/          FastAPI application, migrations, and upload storage
frontend/         React + Vite user interface with installed node_modules
infra/            Docker Compose definitions for PostgreSQL, Redis, Neo4j, Redpanda, and Marquez
scripts/          Active preprocessing, import, eventing, and graph-maintenance tools
data/
  raw/            Original Athena and DataGalaxy extracts
  processed/      Cleaned catalog files consumed by import workflows
  samples/        DQC sample files
docs/             Architecture and project documentation
legacy/           Historical implementations, notebooks, superseded scripts, and generated reports
.venv/            Existing Python environment retained during the project move
```

## Run The Active Application

Backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload --app-dir backend
```

Frontend:

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1 --port 5176
```

Infrastructure:

```powershell
docker compose -f infra/docker-compose.yml up -d
```

The architecture guide is in [docs/architecture/FAST_HYBRID_SEARCH_ARCHITECTURE.md](docs/architecture/FAST_HYBRID_SEARCH_ARCHITECTURE.md).
Agent readiness and publish-control guidance is in [docs/AGENT_READINESS.md](docs/AGENT_READINESS.md).
