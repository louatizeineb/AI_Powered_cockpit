from pathlib import Path
import os
import pandas as pd
from sqlalchemy import create_engine

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql+psycopg2://postgres:louatiza@localhost/DataGalaxy_tables",
)

INPUT_FILES = [
    Path("../Quality_Topic_extract.parquet"),
]

TABLE_NAME = "DQC"


def main() -> None:
    missing = [str(path) for path in INPUT_FILES if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing input file(s): " + ", ".join(missing))

    frames = []
    for path in INPUT_FILES:
        print(f"Reading {path}...")
        df = pd.read_parquet(path)
        print(f"  {len(df)} rows, {len(df.columns)} columns")
        frames.append(df)

    dqc = pd.concat(frames, ignore_index=True)

    print(f"\nExporting {len(dqc)} rows to PostgreSQL table \"{TABLE_NAME}\"...")
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)

    dqc.to_sql(
        TABLE_NAME,
        engine,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=1000,
    )

    print("Done.")


if __name__ == "__main__":
    main()
