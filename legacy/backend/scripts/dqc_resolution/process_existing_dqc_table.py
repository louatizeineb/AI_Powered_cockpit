from __future__ import annotations
from pathlib import Path
import os, sys
sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from app.db import SessionLocal
from app.config import get_settings
from app.dqc.resolution.service import process_many

settings = get_settings()

def main():
    table = os.getenv("DQC_TABLE", settings.dqc_default_table)
    limit = int(os.getenv("LIMIT", "1000"))
    with SessionLocal() as db:
        rows = db.execute(text(f'SELECT * FROM "{table}" LIMIT :limit'), {"limit": limit}).mappings().all()
    events = [{str(k).lower(): v for k, v in dict(r).items()} for r in rows]
    print(process_many(events, source_system=f"database:{table}"))

if __name__ == "__main__":
    main()
