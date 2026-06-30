from __future__ import annotations


def is_forbidden_join(raw_column_name: str | None, canonical_field: str | None) -> bool:
    """Return true when a forbidden raw column is mapped to an entity identifier."""

    return raw_column_name == "v_ident_works" and canonical_field in {
        "node_id",
        "parent_node_id",
        "src_node_id",
        "tgt_node_id",
    }
