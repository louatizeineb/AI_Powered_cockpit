from __future__ import annotations
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from app.db import SessionLocal
from app.config import get_settings
from app.catalog.path_parser import parse_path_full

settings = get_settings()

TABLES = [
    (settings.catalog_source_table, "Source"),
    (settings.catalog_container_table, "Container"),
    (settings.catalog_structure_table, "Structure"),
    (settings.catalog_field_table, "Field"),
]


def main():
    total = 0
    with SessionLocal() as db:
        db.execute(text("TRUNCATE TABLE catalog_node_embeddings, catalog_path_index RESTART IDENTITY"))
        for table, level in TABLES:
            print(f"Indexing {table} as {level}...")
            rows = db.execute(text(f"""
                SELECT node_id, path_full
                FROM {table}
                WHERE node_id IS NOT NULL AND path_full IS NOT NULL
            """)).mappings().all()
            for row in rows:
                parsed = parse_path_full(row["path_full"])
                db.execute(text("""
                    INSERT INTO catalog_path_index(
                        entity_table, entity_level, node_id, raw_path_full, normalized_path,
                        app_code_from_path, leaf_name, parent_name, path_depth, path_segments, path_tokens
                    ) VALUES (
                        :entity_table, :entity_level, :node_id, :raw_path_full, :normalized_path,
                        :app_code_from_path, :leaf_name, :parent_name, :path_depth, :path_segments, :path_tokens
                    )
                """), {
                    "entity_table": table,
                    "entity_level": level,
                    "node_id": row["node_id"],
                    **parsed,
                })
                total += 1
            db.commit()
            print(f"  indexed {len(rows)} rows")
    print(f"Done. Total indexed: {total}")

if __name__ == "__main__":
    main()
