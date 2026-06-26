# Agent Readiness

This project has two agent families:

- **Migration V2 governance agents**: orchestrate metadata migration evidence, proposals, approval gates, candidate graph checks, benchmarks, and publish dry-runs.
- **DQC Resolution Agent**: explains DQC matching evidence and proposes reviewer actions for resolved matches and DLQ events.

## Readiness Position

The agents are designed for governed local publication workflows, not autonomous production mutation.

Agents may:

- inspect registered evidence;
- call allowlisted deterministic tools;
- produce proposals, rationale, missing evidence, and reviewer questions;
- persist proposal/audit records;
- explain next steps in plain language.

Agents must not:

- approve or reject DQC matches from chat;
- publish a graph from chat;
- invent catalog nodes or relationship endpoints;
- bypass typed tool contracts;
- apply queue decisions without reviewer identity and rationale.

## Control Model

Migration V2 uses versioned manifests, typed tool payloads, and `AllowlistedToolRuntime`.
The DQC agent now follows the same pattern with a manifest, typed tool registry, deterministic proposals, and additive proposal persistence.

Human-controlled endpoints remain the only path for review actions:

- DQC review: `/dqc-resolution/review/{resolved_id}/approve` and `/reject`
- Migration queue decisions: `/migration-v2/exports/{export_id}/validation-queue/{issue_id}/decision`
- Publish actions: `/migration-v2/exports/{export_id}/actions/publish`

## Evidence Lifecycle

1. Deterministic processing creates normalized records, candidates, findings, queue items, or DLQ rows.
2. Agents retrieve bounded evidence and produce proposals.
3. Proposals include recommended action, confidence, rationale, missing evidence, guardrails, and next steps.
4. Reviewers apply decisions through explicit review or governance endpoints.
5. Readiness is recalculated, candidate graph/search checks run, and publish dry-run must pass before activation.

## Demo Script

1. Start infrastructure and backend.
2. Upload or connect a DQC sample.
3. Ask the DQC agent about unresolved events and resolved matches.
4. Apply one DQC review decision through the review endpoint or UI.
5. Open Migration Governance and ask the assistant about pending queue issues.
6. Recalculate readiness.
7. Validate the trusted graph.
8. Refresh candidate search and run the benchmark.
9. Run publish dry-run.
10. Publish only with explicit approval when all gates are ready.

## Known Limits

- The local system assumes trusted operators and local infrastructure; it is not an internet-exposed SaaS security boundary.
- DQC agent proposal tables are additive; apply `backend/migrations/sql/006_dqc_agent_governance.sql` before expecting persisted DQC proposal IDs.
- Migration publish confidence still depends on real Postgres, Neo4j, Redis, and benchmark checks passing in the target environment.
