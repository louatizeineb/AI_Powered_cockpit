from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT_DIR = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT_DIR / "data" / "raw" / "athena"
INPUT_FILE = "usag_usage.csv"
OUTPUT_DIR = ROOT_DIR / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

USAGE_RENAME = {
    "v_tech_ident_entt": "usage_uuid",
    "v_func_name_entt": "usage_name",
    "v_tech_name_entt": "usage_tech_name",
    "v_path": "usage_path",
    "v_type_path": "usage_type_path",
    "v_type_entt": "usage_kind",
    "v_status_entt": "status",
    "v_lvl_class": "classification_level",
    "v_lvl_class_dlk": "classification_level_dlk",
    "v_class_cmnt": "classification_comment",
    "n_perc_progres_doc_class": "doc_pct_classification",
    "n_perc_progres_doc_dacp": "doc_pct_dacp",
    "n_perc_progres_doc_glos": "doc_pct_glossary",
    "n_perc_progres_doc_output": "doc_pct_output",
    "n_perc_progres_doc_dico_glos": "doc_pct_dict_glossary",
    "n_perc_progres_doc_dico_us": "doc_pct_dict_usage",
    "n_perc_progres_doc_us_glos": "doc_pct_usage_glossary",
    "n_perc_progres_doc_storg": "doc_pct_storage",
    "n_perc_progres_doc_prcs_input": "doc_pct_process_input",
    "n_perc_progres_doc_prcs_output": "doc_pct_process_output",
    "n_perc_progres_doc_oprl_us": "doc_pct_operational_usage",
    "n_perc_calc_autom_applic_doc": "doc_pct_auto_computed",
    "n_perc_targ_doc": "doc_pct_target",
    "n_perc_applic_doc_dlk": "doc_pct_applied_dlk",
    "c_applic": "app_code",
    "c_entt_oprl": "operational_entity_code",
    "c_base_applic": "app_base_code",
    "v_applic_base_name": "app_base_name",
    "v_applic_base_cpc": "app_base_cpc",
    "v_applic_base_doma": "app_base_domain",
    "v_applic_criticity": "app_criticality",
    "v_applic_stt": "app_status",
    "v_applic_stt_nextgen": "app_status_nextgen",
    "v_strat_applic": "app_strategic_status",
    "v_host_mode": "hosting_mode",
    "v_prodn_doma": "production_domain",
    "v_oprl_entt": "operational_entity",
    "v_freq": "update_frequency",
    "v_cmnt_freq": "frequency_comment",
    "v_oprl_purge": "operational_purge_rule",
    "b_gdpr_pers_data": "is_gdpr_personal",
    "b_gdpr_sensi_data": "is_gdpr_sensitive",
    "b_loca_data": "is_localized_data",
    "b_elig_data_qlty_dashboard": "is_eligible_dq_dashboard",
    "v_qlty_reqr": "quality_requirements",
    "v_qlty_crit": "quality_criteria",
    "v_doc_criy": "documentation_criticality",
    "v_cmnt_stt": "status_comment",
    "n_accept_thres": "acceptance_threshold",
    "v_contains_dacp": "contains_dacp_data",
    "v_data_retention_per": "data_retention_period",
    "v_purge_rule": "purge_rule",
    "v_purge_featr_avlb": "purge_feature_available",
    "v_purge_activ_mode": "purge_activation_mode",
    "v_oper_purge": "operational_purge_status",
    "v_purge_freq": "purge_frequency",
    "v_purge_arbitration": "purge_arbitration",
    "v_val_purge_methd": "purge_method_validated",
    "v_class_propagation": "classification_propagation",
    "v_dacp_propagation": "dacp_propagation",
    "v_dependency_othr_db": "dependency_other_db",
    "v_underlying": "underlying_data",
    "v_dataset": "dataset_ref",
    "v_dataset_filiere": "dataset_filiere",
    "v_ctct_it": "it_contact",
    "v_bpi_prcs_resp": "bpi_process_owner",
    "v_justif": "justification",
    "v_en_l": "english_label",
    "v_perim": "perimeter",
    "v_regroup_fonc": "functional_grouping",
    "v_ident_us": "usage_link_id",
    "v_prgl_name_edit": "programming_language",
    "b_ctrl_opt": "is_optional_control",
    "s_cre_entt": "created_at",
    "s_last_modif_entt": "updated_at",
    "d_valid": "validated_at",
    "d_review_data_dacp": "dacp_review_date",
    "d_review_data_class": "classification_review_date",
    "d_updt_perc_doc": "doc_pct_last_updated_at",
    "d_release": "release_date",
    "v_summary": "summary",
    "v_desc_entt": "description",
    "v_drct_prnt_entt_ident": "parent_uuid",
    "v_drct_prnt_entt_type": "parent_type",
    "v_drct_prnt_entt_data_type": "parent_data_type",
}

BOOLEAN_COLUMNS = {
    "is_gdpr_personal",
    "is_gdpr_sensitive",
    "is_localized_data",
    "is_eligible_dq_dashboard",
    "is_optional_control",
}
DATE_COLUMNS = {
    "validated_at",
    "dacp_review_date",
    "classification_review_date",
    "doc_pct_last_updated_at",
    "release_date",
}
TIMESTAMP_COLUMNS = {"created_at", "updated_at"}
VALID_STATUSES = {"Proposed", "Validated", "Deprecated", "Obsolete"}

BASE_OUTPUT_COLUMNS = list(USAGE_RENAME.values())
HELPER_COLUMNS = [
    "doc_pct_global",
    "status_score",
    "usage_quality_score",
    "usage_quality_status",
    "hierarchy_level",
    "hierarchy_name",
    "source_file",
]
OUTPUT_COLUMNS = BASE_OUTPUT_COLUMNS + HELPER_COLUMNS


def read_usage_csv(path: Path) -> pl.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    df = pl.read_csv(path, infer_schema_length=None, ignore_errors=True)
    print(f"[READ] {path}: {df.height} rows × {df.width} cols")
    return df


def apply_usage_renames(df: pl.DataFrame) -> pl.DataFrame:
    existing = {old: new for old, new in USAGE_RENAME.items() if old in df.columns}
    missing = sorted(set(USAGE_RENAME) - set(existing))
    if missing:
        print(f"[INFO] Missing expected raw columns: {len(missing)}")
    return df.rename(existing)


def keep_frozen_schema_columns(df: pl.DataFrame) -> pl.DataFrame:
    keep = [c for c in BASE_OUTPUT_COLUMNS if c in df.columns]
    return df.select(keep)


def strip_and_nullify_strings(df: pl.DataFrame) -> pl.DataFrame:
    exprs = []
    for c in df.columns:
        if df[c].dtype == pl.Utf8:
            cleaned = pl.col(c).str.strip_chars()
            exprs.append(pl.when(cleaned == "").then(None).otherwise(cleaned).alias(c))
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def normalize_booleans(df: pl.DataFrame) -> pl.DataFrame:
    exprs = []
    for c in df.columns:
        if c in BOOLEAN_COLUMNS:
            val = pl.col(c).cast(pl.Utf8).str.to_lowercase().str.strip_chars()
            exprs.append(
                pl.when(val.is_in(["true", "1", "oui", "yes", "y", "vrai"])).then(True)
                .when(val.is_in(["false", "0", "non", "no", "n", "faux"])).then(False)
                .otherwise(None)
                .cast(pl.Boolean)
                .alias(c)
            )
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def normalize_numbers(df: pl.DataFrame) -> pl.DataFrame:
    exprs = []
    for c in df.columns:
        if c.startswith("doc_pct") and c != "doc_pct_last_updated_at" or c == "acceptance_threshold":
            exprs.append(
                pl.col(c)
                .cast(pl.Utf8)
                .str.replace_all(",", ".")
                .str.replace_all("%", "")
                .cast(pl.Float64, strict=False)
                .alias(c)
            )
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def normalize_dates(df: pl.DataFrame) -> pl.DataFrame:
    exprs = []
    for c in df.columns:
        if c in TIMESTAMP_COLUMNS:
            exprs.append(pl.col(c).cast(pl.Utf8).str.strptime(pl.Datetime, strict=False).alias(c))
        elif c in DATE_COLUMNS:
            exprs.append(pl.col(c).cast(pl.Utf8).str.strptime(pl.Date, strict=False).alias(c))
        else:
            exprs.append(pl.col(c))
    return df.select(exprs)


def add_quality_helpers(df: pl.DataFrame) -> pl.DataFrame:
    doc_cols = [
        c for c in df.columns
        if c.startswith("doc_pct") and c != "doc_pct_last_updated_at"
    ]

    if doc_cols:
        df = df.with_columns(
            pl.mean_horizontal([
                pl.col(c).cast(pl.Float64, strict=False).fill_null(0.0)
                for c in doc_cols
            ]).round(2).alias("doc_pct_global")
        )
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("doc_pct_global"))

    if "status" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("status") == "Validated").then(pl.lit(100.0))
            .when(pl.col("status") == "Proposed").then(pl.lit(50.0))
            .when(pl.col("status").is_in(["Deprecated", "Obsolete"])).then(pl.lit(0.0))
            .otherwise(pl.lit(25.0))
            .alias("status_score")
        )
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias("status_score"))

    df = df.with_columns(
        (
            pl.col("doc_pct_global").cast(pl.Float64, strict=False).fill_null(0.0) * 0.7
            + pl.col("status_score").cast(pl.Float64, strict=False).fill_null(0.0) * 0.3
        ).round(2).alias("usage_quality_score")
    )

    return df.with_columns(
        pl.when(pl.col("usage_quality_score") >= 80).then(pl.lit("Good"))
        .when(pl.col("usage_quality_score") >= 50).then(pl.lit("Warning"))
        .otherwise(pl.lit("Critical"))
        .alias("usage_quality_status")
    )


def add_metadata(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns([
        pl.lit(0).cast(pl.Int64).alias("hierarchy_level"),
        pl.lit("Usage").alias("hierarchy_name"),
        pl.lit("usag_usage.csv").alias("source_file"),
    ])


def validate_usage(df: pl.DataFrame) -> None:
    if "usage_uuid" not in df.columns:
        raise ValueError("Required primary key column usage_uuid was not found after renaming.")

    null_pk = df.filter(
        pl.col("usage_uuid").is_null()
        | (pl.col("usage_uuid").cast(pl.Utf8).str.strip_chars().str.len_bytes() == 0)
    ).height
    if null_pk:
        print(f"[WARN] {null_pk} rows have empty usage_uuid and will be dropped.")

    if "status" in df.columns:
        bad_statuses = df.filter(
            pl.col("status").is_not_null() & ~pl.col("status").is_in(list(VALID_STATUSES))
        )["status"].unique().to_list()
        if bad_statuses:
            print(f"[WARN] Unexpected status values: {bad_statuses}")


def build_usage_mapping() -> dict:
    return {
        "usage": [{"original": old, "renamed": new} for old, new in USAGE_RENAME.items()],
        "primary_key": ["usage_uuid"],
        "output_columns": OUTPUT_COLUMNS,
    }


def process_usage() -> pl.DataFrame:
    print("#" * 70)
    print("Usage preprocessing pipeline")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("#" * 70)

    df = read_usage_csv(INPUT_DIR / INPUT_FILE)
    df = apply_usage_renames(df)
    df = keep_frozen_schema_columns(df)
    df = strip_and_nullify_strings(df)
    df = normalize_booleans(df)
    df = normalize_numbers(df)
    df = normalize_dates(df)
    df = add_quality_helpers(df)
    df = add_metadata(df)

    validate_usage(df)

    before = df.height
    df = df.filter(pl.col("usage_uuid").cast(pl.Utf8).str.strip_chars().str.len_bytes() > 0)
    # Ensure stable output order and no accidental schema drift.
    final_cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
    df = df.select(final_cols)

    clean_path = OUTPUT_DIR / "usage_clean.csv"
    df.write_csv(clean_path)
    print(f"[SAVE] {clean_path} ({df.height} rows × {df.width} cols)")

    neo4j_df = df
    neo4j_path = OUTPUT_DIR / "usage_neo4j.csv"
    neo4j_df.write_csv(neo4j_path)
    print(f"[SAVE] {neo4j_path} ({neo4j_df.height} rows, all statuses)")

    mapping_path = OUTPUT_DIR / "usage_column_mapping.json"
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(build_usage_mapping(), f, ensure_ascii=False, indent=2)
    print(f"[SAVE] {mapping_path}")

    print("\nPostgreSQL target table: dg_usage")
    print("Primary key: usage_uuid")
    print("Lineage join candidates: usage_link_id, dataset_ref, underlying_data, parent_uuid, app_code")
    return df


if __name__ == "__main__":
    process_usage()
