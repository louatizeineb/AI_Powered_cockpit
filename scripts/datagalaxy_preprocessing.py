"""
DataGalaxy Athena Tables — Preprocessing & Cleaning Pipeline
=============================================================
Client  : BPI / TALAN
Project : AI-Powered Data Quality Cockpit
Author  : Generated from DataGalaxy schema documentation

Tables processed (in hierarchy order):
  1. diso_dico_source    → SOURCE nodes
  2. dict_dico_container → CONTAINER nodes
  3. dist_dico_structure → STRUCTURE nodes
  4. difi_dico_field     → FIELD nodes
  5. lien_link_entt      → [:IMPLEMENTS] relationships (Field → BusinessTerm MOM)

Output:
  - 5 cleaned Parquet files (ready for Neo4j / Marquez ingestion)
  - 1 JSON schema mapping file (old → new column names + metadata)
  - 1 summary report printed to stdout
"""

import polars as pl
import json
import os
from pathlib import Path
from datetime import datetime

# ─── CONFIG ──────────────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT_DIR / "data" / "raw" / "athena"
OUTPUT_DIR = ROOT_DIR / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

READ_OPTS = {"infer_schema_length": None}  # keep all as string on first pass

# ─── COLUMN RENAME MAPS ───────────────────────────────────────────────────────
# Convention for renamed columns:
#   node_id          → technical UUID (primary key in DataGalaxy)
#   workspace_id     → org-level UUID (same across ALL rows, not a join key!)
#   version_id       → version UUID
#   name_label       → functional/business display name
#   name_tech        → technical name as-is in source system
#   path_*           → position in the DataGalaxy tree
#   entity_type      → fine-grained type (Table, Topic, Field, BusinessTerm…)
#   data_type        → coarse level (Source / Container / Structure / Field)
#   status           → Proposed / Validated / Deprecated / Obsolete
#   description      → free-text description
#   summary          → short summary
#   doc_pct          → % of documentation completed (0–100)
#   created_at       → creation timestamp
#   updated_at       → last modification timestamp
#   validated_at     → validation date
#   export_date      → date of Athena export (same in all tables = 2026-03-06)
#   children_count   → number of direct child objects
#   parent_node_id   → FK → parent's node_id (join key up the hierarchy)
#   parent_type      → type of parent entity
#   app_code         → short application code (MKD, AAS, ABL…)
#   techno           → technology string (Oracle, Kafka, PostgreSQL…)
#   tech_code        → short technology code
#   host_mode        → Cloud / On-premise / Hybrid
#   security_level   → classification level (Confidentiel, Public, Interne)
#   security_comment → free-text classification comment
#   train_app        → training application linked
#   dlk_api_path     → relative path in DataGalaxy REST API
#   dlk_url          → direct URL to this object's page in DataGalaxy UI
#   is_mandatory     → field is NOT NULL / mandatory
#   is_primary_key   → field is a primary key
#   is_foreign_key   → field is a foreign key
#   is_technical     → field is a purely technical column (non-business)
#   is_local_data    → field is local to the application (not shared)
#   is_golden_source → this field is the authoritative/reference version
#   is_gdpr_personal → RGPD: contains personally identifiable data
#   is_gdpr_sensitive→ RGPD Art.9: contains sensitive data (health, ethnic origin…)
#   col_data_type    → SQL/source data type (String, Integer, Date, Timestamp…)
#   col_size         → column size (e.g. 50 for VARCHAR(50))
#   col_type_size    → col type + size as a text string
#   class_propagation→ how classification propagates to children/links
#   dacp_propagation → how DACP policy propagates
#   primary_key_name → name of the primary key column in this structure
#   tech_comment     → free-text technical comment
#   kafka_*          → Kafka-specific quality flags
#   doc_pct_func     → % of fields with functional topic documented
#   doc_pct_data     → % of fields with functional data documented
#   doc_pct_label_fr → % of fields with a French label
#   doc_pct_glossary → % of fields linked to business glossary
#   doc_pct_source   → % of fields with a traced data source
#   # LINK TABLE specific
#   src_node_id      → UUID of the source entity of the link (= Field)
#   src_name_label   → functional name of the source entity
#   src_name_tech    → technical name of the source entity
#   src_entity_type  → type of source entity (Column, Field)
#   src_data_type    → data type of source entity (always 'Field')
#   link_type        → nature of the relationship (Implements, IsRelatedTo…)
#   tgt_node_id      → UUID of the target entity (BusinessTerm MOM)
#   tgt_name_label   → functional name of the target (business term label)
#   tgt_name_tech    → technical name of the target
#   tgt_entity_type  → type of target (BusinessTerm)
#   tgt_data_type    → data type of target (Property)
#   tgt_path_type    → path type of target in DataGalaxy tree
#   tgt_path         → full path of the target in the MOM hierarchy


RENAME_SOURCE = {
    "d_extract":          "export_date",
    "v_ident_works":      "workspace_id",
    "v_tech_ident_entt":  "node_id",
    "v_ident_vers":       "version_id",
    "v_func_name_entt":   "name_label",
    "v_tech_name_entt":   "name_tech",
    "v_path":             "path_full",
    "v_type_path":        "path_type",
    "v_type_entt":        "entity_type",
    "v_data_type_entt":   "data_type",
    "v_loct_entt_dlk":    "dlk_api_path",
    "v_url_entt_dlk":     "dlk_url",
    "n_chil_count":       "children_count",
    "v_status_entt":      "status",
    "v_desc_entt":        "description",
    "s_cre_entt":         "created_at",
    "s_last_modif_entt":  "updated_at",
    "n_perc_doc":         "doc_pct",
    "v_class_cmnt":       "security_comment",
    "c_applic":           "app_code",
    "v_curr_techno":      "techno",
    "d_valid":            "validated_at",
    "v_host_mode":        "host_mode",
    "v_lvl_class":        "security_level",
    "c_techno":           "tech_code",
    "v_train_applic":     "train_app",
}

RENAME_CONTAINER = {
    "d_extract":                "export_date",
    "v_ident_works":            "workspace_id",
    "v_tech_ident_entt":        "node_id",
    "v_ident_vers":             "version_id",
    "v_func_name_entt":         "name_label",
    "v_tech_name_entt":         "name_tech",
    "v_path":                   "path_full",
    "v_type_path":              "path_type",
    "v_type_entt":              "entity_type",
    "v_data_type_entt":         "data_type",
    "v_loct_entt_dlk":          "dlk_api_path",
    "v_url_entt_dlk":           "dlk_url",
    "n_chil_count":             "children_count",
    "v_status_entt":            "status",
    "v_desc_entt":              "description",
    "s_cre_entt":               "created_at",
    "s_last_modif_entt":        "updated_at",
    "n_perc_doc":               "doc_pct",
    "v_class_cmnt":             "security_comment",
    "c_applic":                 "app_code",
    "v_curr_techno":            "techno",
    "d_valid":                  "validated_at",
    "v_host_mode":              "host_mode",
    "v_lvl_class":              "security_level",
    "c_techno":                 "tech_code",
    "v_train_applic":           "train_app",
    "v_drct_prnt_entt_ident":   "parent_node_id",
    "v_drct_prnt_entt_type":    "parent_type",
    "v_drct_prnt_entt_data_type": "parent_data_type",
}

RENAME_STRUCTURE = {
    "d_extract":                "export_date",
    "v_ident_works":            "workspace_id",
    "v_tech_ident_entt":        "node_id",
    "v_ident_vers":             "version_id",
    "v_func_name_entt":         "name_label",
    "v_tech_name_entt":         "name_tech",
    "v_path":                   "path_full",
    "v_type_path":              "path_type",
    "v_type_entt":              "entity_type",
    "v_data_type_entt":         "data_type",
    "v_loct_entt_dlk":          "dlk_api_path",
    "v_url_entt_dlk":           "dlk_url",
    "n_chil_count":             "children_count",
    "v_status_entt":            "status",
    "v_summary":                "summary",
    "v_desc_entt":              "description",
    "s_cre_entt":               "created_at",
    "s_last_modif_entt":        "updated_at",
    "d_valid":                  "validated_at",
    "v_train_applic":           "train_app",
    "n_perc_doc":               "doc_pct",
    "v_primr_key_tech_name":    "primary_key_name",
    "v_tech_cmnt":              "tech_comment",
    "v_drct_prnt_entt_ident":   "parent_node_id",
    "v_drct_prnt_entt_type":    "parent_type",
    "v_drct_prnt_entt_data_type": "parent_data_type",
    "v_type_topic":             "kafka_topic_type",
    "n_perc_fonc_topic":        "doc_pct_func",
    "n_perc_fonc_data":         "doc_pct_data",
    "n_perc_lib_fr":            "doc_pct_label_fr",
    "n_perc_glos_data":         "doc_pct_glossary",
    "n_perc_sourc_data":        "doc_pct_source",
    "b_topic_conf_ent":         "kafka_conforms_to_entity",
    "b_topic_exh_data":         "kafka_data_exhaustive",
    "b_topic_desc":             "kafka_has_description",
    "b_event_conf_schema":      "kafka_schema_registry_compliant",
}

RENAME_FIELD = {
    "d_extract":                "export_date",
    "v_ident_works":            "workspace_id",
    "v_tech_ident_entt":        "node_id",
    "v_ident_vers":             "version_id",
    "v_func_name_entt":         "name_label",
    "v_tech_name_entt":         "name_tech",
    "v_path":                   "path_full",
    "v_type_path":              "path_type",
    "v_type_entt":              "entity_type",
    "v_data_type_entt":         "data_type",
    "v_loct_entt_dlk":          "dlk_api_path",
    "v_url_entt_dlk":           "dlk_url",
    "n_chil_count":             "children_count",
    "v_status_entt":            "status",
    "v_summary":                "summary",
    "v_desc_entt":              "description",
    "s_cre_entt":               "created_at",
    "s_last_modif_entt":        "updated_at",
    "c_applic":                 "app_code",
    "d_valid":                  "validated_at",
    "v_tech_cmnt":              "tech_comment",
    "v_lvl_class":              "security_level",
    "v_class_cmnt":             "security_comment",
    "v_train_applic":           "train_app",
    "b_mdt":                    "is_mandatory",
    "b_primr_key":              "is_primary_key",
    "b_frgn_key":               "is_foreign_key",
    "b_tech_data":              "is_technical",
    "b_gdpr_pers_data":         "is_gdpr_personal",
    "b_gdpr_sensi_data":        "is_gdpr_sensitive",
    "v_class_propagation":      "class_propagation",
    "v_dacp_propagation":       "dacp_propagation",
    "n_col_size":               "col_size",
    "v_col_data_type":          "col_data_type",
    "v_col_data_type_size":     "col_type_size",
    "v_drct_prnt_entt_ident":   "parent_node_id",
    "v_drct_prnt_entt_type":    "parent_type",
    "v_drct_prnt_entt_data_type": "parent_data_type",
    "b_loca_data":              "is_local_data",
    "v_donnee_golden_source":   "is_golden_source",
}

RENAME_LINK = {
    "d_extract":                    "export_date",
    "v_ident_works":                "workspace_id",
    "v_tech_ident_entt":            "src_node_id",
    "v_func_name_entt":             "src_name_label",
    "v_tech_name_entt":             "src_name_tech",
    "v_type_entt":                  "src_entity_type",
    "v_data_type_entt":             "src_data_type",
    "v_desc_nature_link":           "link_type",
    "v_ident_tech_entt_output":     "tgt_node_id",
    "v_name_func_entt_output":      "tgt_name_label",
    "v_name_tech_entt_output":      "tgt_name_tech",
    "v_name_type_entt_output":      "tgt_entity_type",
    "v_name_data_type_entt_output": "tgt_data_type",
    "v_name_type_path_entt_output": "tgt_path_type",
    "v_name_path_entt_output":      "tgt_path",
}

ALL_RENAMES = {
    "source":    RENAME_SOURCE,
    "container": RENAME_CONTAINER,
    "structure": RENAME_STRUCTURE,
    "field":     RENAME_FIELD,
    "link":      RENAME_LINK,
}

# ─── COLUMNS TO DROP (zero-value, always constant, or admin noise) ─────────────
# workspace_id = same UUID across all rows → not a join key, just org identity
# data_type    = constant per table (always 'Source', 'Container', etc.)
# dlk_api_path = internal DataGalaxy REST path, not needed for lineage graph
# After rename, we also drop workspace_id to reduce cardinality noise.



# ─── STATUS VALUES (enum validation) ─────────────────────────────────────────
VALID_STATUSES = {"Proposed", "Validated", "Deprecated", "Obsolete"}

# ─── KAFKA FLAG VALUES ────────────────────────────────────────────────────────
VALID_KAFKA = {"Oui", "Non", "Non contrôlé"}

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def safe_read(path: Path) -> pl.DataFrame | None:
    if not path.exists():
        print(f"  [SKIP] {path.name} not found — skipping.")
        return None
    df = pl.read_csv(path, infer_schema_length=None)
    print(f"  [READ] {path.name}: {df.shape[0]} rows × {df.shape[1]} cols")
    return df


def apply_renames(df: pl.DataFrame, rename_map: dict) -> pl.DataFrame:
    existing = {k: v for k, v in rename_map.items() if k in df.columns}
    return df.rename(existing)




def strip_strings(df: pl.DataFrame) -> pl.DataFrame:
    """Trim leading/trailing whitespace from all string columns."""
    exprs = []
    for c in df.columns:
        if df[c].dtype == pl.Utf8:
            exprs.append(pl.col(c).str.strip_chars())
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def normalize_booleans(df: pl.DataFrame) -> pl.DataFrame:
    """
    Cast boolean-ish string columns to proper boolean.
    DataGalaxy stores booleans as 'true'/'false' (lowercase strings).
    Kafka flags use 'Oui'/'Non'/'Non contrôlé' — those stay as strings.
    Skips columns already cast to Boolean by Polars on read.
    """
    bool_cols = {
        "is_mandatory", "is_primary_key", "is_foreign_key", "is_technical",
        "is_local_data", "is_golden_source", "is_gdpr_personal", "is_gdpr_sensitive",
    }
    exprs = []
    for c in df.columns:
        if c in bool_cols:
            if df[c].dtype == pl.Boolean:
                exprs.append(pl.col(c))  # already correct
            else:
                exprs.append(
                    pl.when(pl.col(c).cast(pl.Utf8).str.to_lowercase() == "true").then(True)
                    .when(pl.col(c).cast(pl.Utf8).str.to_lowercase() == "false").then(False)
                    .otherwise(None)
                    .alias(c)
                    .cast(pl.Boolean)
                )
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def normalize_doc_pct(df: pl.DataFrame) -> pl.DataFrame:
    """Cast doc_pct* columns to Float32 (they come in as strings)."""
    pct_cols = [c for c in df.columns if c.startswith("doc_pct")]
    if not pct_cols:
        return df
    exprs = []
    for c in df.columns:
        if c in pct_cols:
            exprs.append(
                pl.col(c).cast(pl.Float32, strict=False).alias(c)
            )
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def normalize_timestamps(df: pl.DataFrame) -> pl.DataFrame:
    """
    Two timestamp formats coexist in the export:
      - Full: '2025-12-03 12:04:02.690'   (field table)
      - Short: '04:02.6'                   (container table — broken partial)
    We try ISO parse; if it fails, we leave as string and flag it.
    """
    ts_cols = [c for c in df.columns if c in ("created_at", "updated_at")]
    if not ts_cols:
        return df
    exprs = []
    for c in df.columns:
        if c in ts_cols:
            exprs.append(
                pl.col(c)
                .str.strptime(pl.Datetime, format="%Y-%m-%d %H:%M:%S%.f", strict=False)
                .alias(c)
            )
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def normalize_dates(df: pl.DataFrame) -> pl.DataFrame:
    """Cast date columns (export_date, validated_at) to pl.Date."""
    date_cols = [c for c in df.columns if c in ("export_date", "validated_at")]
    if not date_cols:
        return df
    exprs = []
    for c in df.columns:
        if c in date_cols:
            exprs.append(
                pl.col(c)
                .str.strptime(pl.Date, format="%Y-%m-%d", strict=False)
                .alias(c)
            )
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def add_hierarchy_level(df: pl.DataFrame, level: int, level_name: str) -> pl.DataFrame:
    """Add explicit hierarchy metadata so downstream tools know the level."""
    return df.with_columns([
        pl.lit(level).alias("hierarchy_level"),
        pl.lit(level_name).alias("hierarchy_name"),
    ])


def validate_status(df: pl.DataFrame, table_name: str) -> None:
    if "status" not in df.columns:
        return
    bad = df.filter(
        pl.col("status").is_not_null() &
        ~pl.col("status").is_in(list(VALID_STATUSES))
    )
    if bad.shape[0] > 0:
        vals = bad["status"].unique().to_list()
        print(f"  [WARN] {table_name}: unexpected status values: {vals}")


def report_nulls(df: pl.DataFrame, table_name: str) -> None:
    high_null = [
        c for c in df.columns
        if df[c].null_count() / df.shape[0] > 0.9
    ]
    if high_null:
        print(f"  [INFO] {table_name}: >90% null columns: {high_null}")


def filter_validated_only(df: pl.DataFrame) -> pl.DataFrame:
    """
    Per recommendation in DataGalaxy docs: the vast majority of rows are
    'Proposed' with doc_pct = 0. For Neo4j lineage, keep Validated only.
    Call this only on the Neo4j-targeted output, not the full clean version.
    """
    if "status" not in df.columns:
        return df
    return df.filter(pl.col("status") == "Validated")


# ─── MAIN PIPELINE ────────────────────────────────────────────────────────────

def process_table(
    name: str,
    filename: str,
    rename_map: dict,
    hierarchy_level: int,
    hierarchy_name: str,
    extra_transforms=None
) -> pl.DataFrame | None:

    print(f"\n{'='*60}")
    print(f"  TABLE: {name.upper()} ({filename})")
    print(f"{'='*60}")

    path = INPUT_DIR / filename
    df = safe_read(path)
    if df is None:
        return None

    # 1. Rename columns
    df = apply_renames(df, rename_map)
    print(f"  → Renamed {len(rename_map)} columns")

    # 3. Strip strings
    df = strip_strings(df)

    # 4. Normalize types
    df = normalize_booleans(df)
    df = normalize_doc_pct(df)
    df = normalize_timestamps(df)
    df = normalize_dates(df)

    # 5. Custom transforms
    if extra_transforms:
        df = extra_transforms(df)

    # 6. Add hierarchy metadata
    df = add_hierarchy_level(df, hierarchy_level, hierarchy_name)

    # 7. Validations
    validate_status(df, name)
    report_nulls(df, name)

    # 8. Stats
    print(f"  → Output: {df.shape[0]} rows × {df.shape[1]} cols")
    print(f"  → Columns: {df.columns}")

    # 9. Save full cleaned version
    out_path = OUTPUT_DIR / f"{name}_clean.parquet"
    df.write_parquet(out_path)
    print(f"  → Saved: {out_path}")

    # 10. Save Neo4j-ready version (Validated only)
    df_neo4j = filter_validated_only(df)
    out_neo4j = OUTPUT_DIR / f"{name}_neo4j.parquet"
    df_neo4j.write_parquet(out_neo4j)
    pct = round(df_neo4j.shape[0] / df.shape[0] * 100, 1) if df.shape[0] > 0 else 0
    print(f"  → Neo4j subset: {df_neo4j.shape[0]} rows ({pct}% of total, Validated only)")

    return df


def extra_structure_transforms(df: pl.DataFrame) -> pl.DataFrame:
    """
    Add a computed column is_kafka_topic to quickly filter Kafka structures.
    Kafka structures have entity_type = 'Topic' or kafka_topic_type is not null.
    """
    if "entity_type" in df.columns:
        df = df.with_columns([
            (pl.col("entity_type") == "Topic").alias("is_kafka_topic")
        ])
    return df


def extra_field_transforms(df: pl.DataFrame) -> pl.DataFrame:
    """
    Add a computed risk_score column for the ML module.
    Score = 0–4 based on: golden_source + personal_data + sensitive_data + mandatory
    """
    score_expr = pl.lit(0)
    for col, weight in [
        ("is_golden_source", 1),
        ("is_gdpr_personal", 1),
        ("is_gdpr_sensitive", 2),
        ("is_mandatory", 1),
    ]:
        if col in df.columns:
            score_expr = score_expr + (
                pl.col(col).cast(pl.Int32, strict=False).fill_null(0) * weight
            )
    df = df.with_columns([score_expr.alias("governance_risk_score")])
    return df


def build_schema_mapping(all_renames: dict) -> dict:
    """Build a JSON-serializable mapping for documentation purposes."""
    mapping = {}
    for table, renames in all_renames.items():
        mapping[table] = [
            {"original": old, "renamed": new}
            for old, new in renames.items()
        ]
    return mapping


def print_join_guide():
    print("""
╔══════════════════════════════════════════════════════════════╗
║              JOIN KEYS & LINEAGE TRAVERSAL GUIDE             ║
╠══════════════════════════════════════════════════════════════╣
║  Hierarchy (strict parent-child, all use same FK pattern):   ║
║                                                              ║
║  SOURCE          node_id  ←──────────────────────────┐       ║
║       ↓ (1:N)                                        │       ║
║  CONTAINER       node_id ← parent_node_id of STRUCT  │       ║
║       ↓ (1:N)                                        │       ║
║  STRUCTURE       node_id ← parent_node_id of FIELD   │       ║
║       ↓ (1:N)                                        │       ║
║  FIELD           node_id ──────────────────────────► link.src_node_id
║       ↓ [:IMPLEMENTS]                                        ║
║  BusinessTerm    tgt_node_id (MOM — external to export)      ║
║                                                              ║
║  Universal join rule:                                        ║
║    parent.node_id = child.parent_node_id                     ║
║                                                              ║
║  Link table join:                                            ║
║    field.node_id = link.src_node_id                          ║
║    link.tgt_node_id → BusinessTerm UUID (MOM, not in export) ║
║                                                              ║
║  ⚠ workspace_id is NOT a join key (same on all rows)         ║
╚══════════════════════════════════════════════════════════════╝
""")


def print_arborescence():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                  DATA ARBORESCENCE IN NEO4J                  ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  (:Source)                                                   ║
║    │  name_label       → 'AAA DATA', 'ABE', 'Kafka'          ║
║    │  app_code         → 'MKD', 'AAS', 'ABL'                 ║
║    │  techno           → Oracle / Kafka / PostgreSQL          ║
║    │  security_level   → Confidentiel / Public / Interne      ║
║    │  status           → Validated (filtered for Neo4j)       ║
║    │                                                          ║
║    └─[:HAS_CONTAINER]──► (:Container)                        ║
║         │  entity_type → Directory / Schema / Database        ║
║         │  name_label  → schema or namespace name            ║
║         │                                                     ║
║         └─[:HAS_STRUCTURE]──► (:Structure)                   ║
║              │  entity_type → Table / Topic / View /          ║
║              │                SubStructure / Document         ║
║              │  is_kafka_topic → True/False (computed)        ║
║              │  kafka_schema_registry_compliant               ║
║              │  doc_pct_glossary, doc_pct_label_fr…          ║
║              │                                                 ║
║              └─[:HAS_FIELD]──► (:Field)                      ║
║                   │  name_tech         → 'D_POSI'             ║
║                   │  name_label        → 'Date position'      ║
║                   │  col_data_type     → Date / String…       ║
║                   │  is_primary_key    → bool                 ║
║                   │  is_gdpr_personal  → bool                 ║
║                   │  is_gdpr_sensitive → bool                 ║
║                   │  is_golden_source  → bool                 ║
║                   │  governance_risk_score → 0–5 (computed)   ║
║                   │                                           ║
║                   └─[:IMPLEMENTS]──► (:BusinessTerm)         ║
║                        tgt_name_label → 'Date de position'   ║
║                        tgt_path       → \\MOM\\Contrat\\...  ║
║                                                              ║
║  Example full path:                                          ║
║  ABE → schéma COMPTA → TABLE_SOLDES → D_POSI                 ║
║       → [:IMPLEMENTS] → 'Date de position' (MOM)             ║
╚══════════════════════════════════════════════════════════════╝
""")


# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{'#'*60}")
    print(f"  DataGalaxy Athena Preprocessing Pipeline")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")

    results = {}

    results["source"] = process_table(
        name="source",
        filename="diso_dico_source.csv",
        rename_map=RENAME_SOURCE,
        hierarchy_level=1,
        hierarchy_name="Source",
    )

    results["container"] = process_table(
        name="container",
        filename="dict_dico_container.csv",
        rename_map=RENAME_CONTAINER,
        hierarchy_level=2,
        hierarchy_name="Container",
    )

    results["structure"] = process_table(
        name="structure",
        filename="dist_dico_structure.csv",
        rename_map=RENAME_STRUCTURE,
        hierarchy_level=3,
        hierarchy_name="Structure",
        extra_transforms=extra_structure_transforms,
    )

    results["field"] = process_table(
        name="field",
        filename="difi_dico_field.csv",
        rename_map=RENAME_FIELD,
        hierarchy_level=4,
        hierarchy_name="Field",
        extra_transforms=extra_field_transforms,
    )

    results["link"] = process_table(
        name="link",
        filename="lien_link_entt.csv",
        rename_map=RENAME_LINK,
        hierarchy_level=0,
        hierarchy_name="Link",
    )

    # Save schema mapping JSON
    schema_map = build_schema_mapping(ALL_RENAMES)
    schema_path = OUTPUT_DIR / "column_mapping.json"
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema_map, f, ensure_ascii=False, indent=2)
    print(f"\n  [JSON] Column mapping saved: {schema_path}")

    print_join_guide()
    print_arborescence()

    print(f"\n{'#'*60}")
    print(f"  Pipeline complete. Outputs in: {OUTPUT_DIR.resolve()}")
    print(f"{'#'*60}\n")
