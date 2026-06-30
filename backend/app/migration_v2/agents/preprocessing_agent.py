AGENT_ROLE = {
    "name": "PreprocessingAgent",
    "mission": "Coordinate deterministic preprocessing into canonical PostgreSQL staging.",
    "tools": ["05_preprocess_to_staging.py", "cleaners.py", "normalizers.py", "type_parsers.py"],
    "requires_human_approval": "yes for repairs or exclusions",
}
