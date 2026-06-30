  # Frontend Contract

  The UI should start with two entry choices:

  1. Connect quality-check database/table
  2. Drag and drop quality-check file: JSON, CSV, Parquet

  ## Connect database

  Call:

  ```http
  POST /dqc/connect/database
  {
    "table_name": "DQC",
    "limit": 1000
  }
  ```

  The backend automatically launches DQC resolution.

  ## Drag and drop

  Call multipart:

  ```http
  POST /dqc/upload
  file=<csv/json/parquet>
  ```

  The backend stores the upload, reads it, normalizes it, validates it, resolves it, and stores logs.

  ## Screens

  - DQC Upload/Connect
  - Resolution Run Logs
  - Resolved Results
  - Human Review Queue
  - DLQ/Unresolved
  - Agent Investigation Panel
  - Catalog Entity Quality Profile

  ## Human validation actions

  ```http
  POST /dqc/review/{resolved_id}/approve
  POST /dqc/review/{resolved_id}/reject
  ```
