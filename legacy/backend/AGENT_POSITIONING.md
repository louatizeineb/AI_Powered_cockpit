# DQC Agent Positioning

The agent is not a free-form autonomous process. It is a fixed-workflow supervisor with controlled tools.

## Input

The agent receives either:

- a raw DQC event
- a normalized DQC event
- a DLQ event id
- a user question about resolved/unresolved events

## Workflow

```text
1. Fuzzy/path candidate generation
2. GraphRAG evidence retrieval
3. Candidate selection
4. Explanation
5. Human validation gate
```

## Tools

- `tool_process_dqc_event(event)`
- `tool_generate_candidates(event)`
- `tool_retrieve_graphrag_evidence(event)`
- `tool_list_unresolved(limit)`
- `tool_list_resolved(limit)`
- `tool_approve_match(resolved_id, reviewer, note)`
- `tool_reject_match(resolved_id, reviewer, reason)`

## Automation responsibility

The agent automates investigation and explanation, not blind final approval.

- High-confidence deterministic matches: auto-attach.
- Medium-confidence matches: agent explains, human accepts/rejects.
- Low-confidence matches: remain DLQ, agent explains why.

## GraphRAG role

GraphRAG retrieves evidence from:

- parsed catalog path_full
- candidate paths
- precomputed embeddings by cosine similarity
- optional Neo4j graph neighbors later

It gives the agent grounded context before explanation.
