import pandas as pd
from pathlib import Path

from sqlalchemy import create_engine

DATA_FOLDER = Path(r"C:\Users\louat\OneDrive\Desktop\v2\renamed")

DATABASE_URL = "postgresql+psycopg2://postgres:change_me@localhost/DataGalaxy_tables"

engine = create_engine(DATABASE_URL)

parque_files = {
    "container": "container_clean.parquet",
    "field": "field_clean.parquet",
    "source": "source_clean.parquet",
    "structure": "structure_clean.parquet",
    "link": "link_clean.parquet",
} 
usage_file = "usage_clean.csv"

for table_name, file_name in parque_files.items():
    path = DATA_FOLDER / file_name

    print(f"Loading {file_name} -> {table_name}")

    df = pd.read_parquet(path)

    # normalize columns
    df.columns = [
        c.lower().replace(" ", "_").replace("-", "_")
        for c in df.columns
    ]

    df.to_sql(
        table_name,
        engine,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=1000,
    )


# Load usage separately (CSV)
usage_path = DATA_FOLDER / usage_file
df_usage = pd.read_csv(usage_path, dtype=str, low_memory=False)
df_usage.to_sql(
    "usage",
    engine,
    if_exists="append",
    index=False,
    method="multi",
    chunksize=1000,
)

print("Import complete.")