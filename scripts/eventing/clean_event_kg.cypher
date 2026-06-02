// Run only on the separate Event Knowledge Graph Neo4j instance, not your catalog graph.
MATCH (n)
DETACH DELETE n;
