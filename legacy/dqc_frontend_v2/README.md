# AI Data Quality Cockpit Frontend v2

This frontend keeps the existing Lineage Explorer and adds a DQC Agent Cockpit for the new backend.

## Main screens

- DQC Upload / Connect
- Resolution Run Logs
- Resolved Results
- Human Review Queue
- DLQ / Unresolved
- Agent Investigation Panel
- Lineage Explorer

## Backend routes used

- `GET /search`
- `GET /lineage/business/{nodeId}`
- `POST /dqc-resolution/connect/database`
- `POST /dqc-resolution/upload`
- `GET /dqc-resolution/resolved`
- `GET /dqc-resolution/unresolved`
- `POST /dqc-resolution/review/{resolved_id}/approve`
- `POST /dqc-resolution/review/{resolved_id}/reject`
- `GET /observability/logs`
- `POST /agent/dqc/chat`
- `POST /agent/dqc/run-workflow`

If your backend uses `/dqc/...` instead of `/dqc-resolution/...`, update `src/api.js`.

## Run

```bash
npm install
npm run dev
```

Optional API base URL:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```
