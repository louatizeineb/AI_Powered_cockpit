import polars as pl
from pathlib import Path

# =========================
# LOAD FILES
# =========================

df_container = pl.read_parquet("./renamed/container_clean.parquet")
df_field     = pl.read_parquet("./renamed/field_clean.parquet")
df_structure = pl.read_parquet("./renamed/structure_clean.parquet")
df_entt      = pl.read_parquet("./renamed/link_clean.parquet")
df_source    = pl.read_parquet("./renamed/source_clean.parquet")
df_usage     = pl.read_csv("./renamed/usage_clean.csv")

tables = {
    "source":    df_source,
    "container": df_container,
    "structure": df_structure,
    "field":     df_field,
    "link":      df_entt,
    "usage":     df_usage,
}

# =========================
# POLARS -> SQLALCHEMY TYPES
# =========================

TYPE_MAPPING = {
    pl.Int8: "Integer",
    pl.Int16: "Integer",
    pl.Int32: "Integer",
    pl.Int64: "BigInteger",

    pl.UInt8: "Integer",
    pl.UInt16: "Integer",
    pl.UInt32: "BigInteger",
    pl.UInt64: "BigInteger",

    pl.Float32: "Float",
    pl.Float64: "Float",

    pl.Boolean: "Boolean",

    pl.Utf8: "Text",
    pl.String: "Text",

    pl.Date: "Date",
    pl.Datetime: "DateTime",

    pl.Time: "Time",

    pl.Duration: "Interval",
}

# =========================
# GENERATE SCHEMA REPORT
# =========================

print("\n" + "=" * 80)
print("COLUMN TYPE REPORT")
print("=" * 80)

for table_name, df in tables.items():

    print(f"\n\nTABLE: {table_name}")
    print("-" * 80)

    for col, dtype in zip(df.columns, df.dtypes):

        nullable = df[col].null_count() > 0

        sqlalchemy_type = TYPE_MAPPING.get(dtype, "Text")

        print(
            f"{col:40} "
            f"{str(dtype):20} "
            f"-> {sqlalchemy_type:12} "
            f"{'NULLABLE' if nullable else 'NOT NULL'}"
        )

# =========================
# AUTO-GENERATE model.py
# =========================

model_lines = []

model_lines.append(
'''from sqlalchemy.orm import declarative_base
from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    Float,
    Boolean,
    Text,
    Date,
    DateTime,
    Time,
)

Base = declarative_base()

'''
)

for table_name, df in tables.items():

    class_name = "".join(word.capitalize() for word in table_name.split("_"))

    model_lines.append(f"\nclass {class_name}(Base):")
    model_lines.append(f'    __tablename__ = "{table_name}"\n')

    model_lines.append(
        "    id = Column(Integer, primary_key=True, autoincrement=True)"
    )

    for col, dtype in zip(df.columns, df.dtypes):

        safe_col = (
            col.lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace("/", "_")
        )

        sqlalchemy_type = TYPE_MAPPING.get(dtype, "Text")

        nullable = df[col].null_count() > 0

        model_lines.append(
            f"    {safe_col} = Column({sqlalchemy_type}, nullable={nullable})"
        )

    model_lines.append("")

# =========================
# SAVE model.py
# =========================

output_path = Path("./model.py")

with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(model_lines))

print("\n")
print("=" * 80)
print(f"Generated model file: {output_path}")
print("=" * 80)