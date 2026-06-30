AGENT_ROLE = {
    "name": "AuditAgent",
    "mission": "Audit candidate graph quality and compare v2 against baseline v0.",
    "tools": ["09_audit_and_compare.py", "graph_auditor.py", "benchmark_service.py"],
    "requires_human_approval": "yes for publish",
}
