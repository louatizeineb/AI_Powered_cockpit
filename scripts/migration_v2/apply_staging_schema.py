from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy import create_engine

from _common import config_section, load_env_config


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQL = ROOT / "backend" / "migrations" / "sql" / "010_migration_v2_staging.sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the migration_v2 PostgreSQL staging schema without psql.")
    parser.add_argument("--env-config", help="Local environment config with a v2.postgres_url value.")
    parser.add_argument(
        "--sql",
        default=str(DEFAULT_SQL),
        help="SQL migration file to apply.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    postgres_url = os.getenv("POSTGRES_URL")
    if args.env_config:
        v2_config = config_section(load_env_config(args.env_config), "v2")
        postgres_url = v2_config.get("postgres_url") or postgres_url
    if not postgres_url:
        raise SystemExit("POSTGRES_URL is required, or pass --env-config with v2.postgres_url.")

    sql_path = Path(args.sql)
    if not sql_path.exists():
        raise SystemExit(f"SQL file not found: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8")
    engine = create_engine(postgres_url, pool_pre_ping=True)
    with engine.begin() as conn:
        cursor = conn.connection.cursor()
        try:
            cursor.execute(sql)
        finally:
            cursor.close()

    print(f"Applied migration_v2 staging schema from {sql_path}")


if __name__ == "__main__":
    main()
