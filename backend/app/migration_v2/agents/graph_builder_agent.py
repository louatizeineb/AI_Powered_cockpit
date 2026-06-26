AGENT_ROLE = {
    "name": "GraphBuilderAgent",
    "mission": "Request deterministic candidate Neo4j graph builds from approved staging.",
    "tools": ["07_build_graph.py", "graph_builder.py", "neo4j_schema.py"],
    "requires_human_approval": "yes before production publish",
}
