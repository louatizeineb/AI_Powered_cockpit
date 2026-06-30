# Athena UI Redesign Preview

## Product Idea

Athena is presented as one connected data-operations product rather than three separate demos. The persistent product bar moves between Quality, Lineage, and Governance while each workspace keeps only the navigation needed for its job.

The design is intentionally calm, dense, and evidence-led. It uses white working surfaces, a neutral canvas, restrained teal actions, compact typography, and familiar controls. The primary question on every screen is: what should the operator do next?

## Workspace Story

### Quality Operations

- Starts with one clear task: bring in quality results.
- Database, file, and pasted data are alternate methods, not three competing panels.
- Results, review, unresolved controls, activity, and the AI analyst are organized as an operational inbox.
- Counts stay visible in the header so the operator always understands workload and progress.

### Lineage Explorer

- The lineage canvas, layout, gestures, expansion behavior, and canvas controls are unchanged.
- Only the surrounding product bar, search panel, toolbar, and metadata panel receive the shared Athena visual language.
- The graph remains the dominant surface and keeps its existing working area.

### Release Governance

- Governance is framed as a release journey: Schema, Decisions, Graph, Search, Release.
- The overview states whether the trusted slice can ship and offers one primary next action.
- Evidence, agents, GraphRAG, and technical history remain available, but do not compete with the release decision.
- Quarantined and review-pending metadata stay visible as governed evidence without entering normal search or traversal.

## Evaluation Checklist

1. Can a first-time operator tell where they are and what to do next within five seconds?
2. Does Quality feel like a task inbox instead of a collection of technical forms?
3. Does Governance explain release readiness without requiring migration-script knowledge?
4. Is the Lineage canvas visually and behaviorally unchanged?
5. Are advanced evidence and agent details available without overwhelming the default view?
6. Does the mobile layout preserve the primary action and important status information?

## Deliberately Deferred

- Final brand identity, logo, and organization-specific color tokens.
- User preferences, notification behavior, and environment switching.
- Full accessibility audit and copy review after the interaction model is approved.
- Additional dashboard visualization. The first preview prioritizes daily operator workflows over decorative analytics.
