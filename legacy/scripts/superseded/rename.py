import pandas as pd
import json
from pathlib import Path

# Folder containing files
folder = Path(r"C:\Users\louat\OneDrive\Desktop\v2\tables athena")


# Load column mapping
with open(folder / "column_mapping.json", "r", encoding="utf-8") as f:
    column_mapping = json.load(f)

# Process all CSV files
for csv_file in folder.glob("*.csv"):
    print(f"Processing: {csv_file.name}")

    # Read CSV
    df = pd.read_csv(csv_file, dtype=str, low_memory=False)

    # Rename columns
    df.rename(columns=column_mapping, inplace=True)

    # Save back (overwrite)
    output_file = csv_file.with_name(csv_file.stem + "_renamed.csv")
    df.to_csv(output_file, index=False)

print("Done.")