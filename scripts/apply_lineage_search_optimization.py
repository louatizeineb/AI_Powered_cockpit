from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.db import postgres_conn  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install and publish the indexed lineage search read model.")
    parser.add_argument("--include-vector", action="store_true", help="Apply the optional pgvector ANN migration.")
    parser.add_argument("--skip-refresh", action="store_true", help="Install schema only without publishing documents.")
    return parser.parse_args()


def apply_sql(path: Path) -> None:
    print(f"Applying {path.name}...")
    sql = path.read_text(encoding="utf-8")
    with postgres_conn() as conn:
        cursor = conn.connection.cursor()
        try:
            cursor.execute(sql)
            conn.connection.commit()
        finally:
            cursor.close()


def main() -> None:
    args = parse_args()
    migrations = BACKEND / "migrations" / "sql"
    apply_sql(migrations / "003_lineage_search_read_model.sql")
    if args.include_vector:
        apply_sql(migrations / "004_pgvector_ann_optional.sql")
    if not args.skip_refresh:
        print("Publishing indexed lineage search documents...")
        with postgres_conn() as conn:
            result = conn.execute(text("SELECT * FROM refresh_lineage_search_documents()")).mappings().one()
            conn.commit()
        print(f"Published graph_version={result['graph_version']} documents={result['document_count']}")
    apply_sql(migrations / "005_lineage_search_indexes.sql")


if __name__ == "__main__":
    main()
