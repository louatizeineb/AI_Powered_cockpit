# Migration V2 Phase 1 Foundation

Phase 1 establishes the durable control plane for the multi-agent migration system. It does not yet turn the role descriptors into autonomous agents or change the active catalog graph.

## Added Components

- PostgreSQL workflow, transition, audit-checkpoint, approval, and tool-execution tables.
- A strict `MigrationRunState` shared by future LangGraph nodes.
- Deterministic export fingerprints and workflow idempotency keys.
- Versioned agent manifests with explicit tools, capabilities, write scopes, limits, and approval requirements.
- A repository for run creation, transitions, checkpoints, approvals, and idempotent tool execution.
- A minimal LangGraph graph proving that typed workflow state can be compiled and checkpointed.
- A CLI entry point for creating or resuming a workflow run.

## Apply The Schema

```powershell
.\.venv\Scripts\python.exe scripts\migration_v2\apply_staging_schema.py `
  --env-config configs\migration_v2\local_env.yaml `
  --sql backend\migrations\sql\014_migration_v2_workflow_orchestration.sql
```

## Create Or Resume A Run

The export must already be registered by `01_register_export.py`.

```powershell
.\.venv\Scripts\python.exe scripts\migration_v2\20_create_workflow_run.py `
  --export-id dg_old_athena_test `
  --env-config configs\migration_v2\local_env.yaml `
  --created-by louat
```

Running the same command again returns the same run because the idempotency key includes the export ID, sorted file hashes, contract version, and workflow version.

## Persistence Layers

`migration_workflow_checkpoint` stores application-level audit snapshots at meaningful migration phases. LangGraph's `PostgresSaver` creates its own low-level execution checkpoint tables when `setup()` is called. Both are intentional: one is operator-facing evidence, while the other supports graph execution recovery.

## Security Boundary

Agent manifests expose named tools only. No agent receives general shell, SQL, or Cypher access. Phase 3 will implement the first three executable agents against these contracts.
