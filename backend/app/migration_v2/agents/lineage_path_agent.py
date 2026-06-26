AGENT_ROLE = {
    "name": "LineagePathAgent",
    "mission": "Generate lineage path read models and explain coverage.",
    "tools": ["08_generate_lineage_paths.py", "lineage_path_builder.py"],
    "requires_human_approval": "conditional on coverage regression",
}
