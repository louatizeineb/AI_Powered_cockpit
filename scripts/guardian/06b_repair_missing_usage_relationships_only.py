from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from neo4j import GraphDatabase


NEO4J_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "change_me")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5000"))

INPUT_FILE = Path(
    os.getenv(
        "BAD_USAGE_REL_FILE",
        r"reports\migration_guardian\fast_usage_lineage_audit\bad_usage_like_relationships.csv",
    )
)

OUTPUT_DIR = Path(
    os.getenv(
        "REPAIR_OUTPUT_DIR",
        r"reports\migration_guardian\missing_usage_relationship_repair",
    )
)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


REL_MAPPING = {
    "IsInputOf": "IS_INPUT_OF",
    "IsOutputOf": "IS_OUTPUT_OF",
    "Uses": "USES",
    "IsUsedBy": "IS_USED_BY",
    "Calls": "CALLS",
    "IsCalledBy": "IS_CALLED_BY",
    "Implements": "IMPLEMENTS",
    "IsImplementedBy": "IS_IMPLEMENTED_BY",
    "Generalizes": "GENERALIZES",
    "Specializes": "SPECIALIZES",
    "IsLinkedTo": "IS_LINKED_TO",
    "Regroups": "REGROUPS",
    "IsPartOfDimension": "IS_PART_OF_DIMENSION",
    "HasForSource": "HAS_FOR_SOURCE",
    "IsSourceOf": "IS_SOURCE_OF",
    "HasForUniverse": "HAS_FOR_UNIVERSE",
    "IsUniverseOf": "IS_UNIVERSE_OF",
    "HasForRecordingSystem": "HAS_FOR_RECORDING_SYSTEM",
    "IsRecordingSystemFor": "IS_RECORDING_SYSTEM_FOR",
    "IsUsageSourceFor": "IS_USAGE_SOURCE_FOR",
    "IsUsageDestinationFor": "IS_USAGE_DESTINATION_FOR",
    "IsUsedForComputationOf": "IS_USED_FOR_COMPUTATION_OF",
    "IsSynonymOf": "IS_SYNONYM_OF",
}


neo4j = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD),
    connection_timeout=60,
    max_connection_lifetime=3600,
)


def normalize_rel(value: Any) -> str:
    if value is None:
        return "RELATED_TO"

    text = str(value).strip()
    if not text:
        return "RELATED_TO"

    if text in REL_MAPPING:
        return REL_MAPPING[text]

    # If the audit file already has ISLINKEDTO or similar,
    # repair known broken normalized names.
    broken = {
        "ISLINKEDTO": "IS_LINKED_TO",
        "HASFORUNIVERSE": "HAS_FOR_UNIVERSE",
        "ISUNIVERSEOF": "IS_UNIVERSE_OF",
        "ISCALLEDBY": "IS_CALLED_BY",
        "ISIMPLEMENTEDBY": "IS_IMPLEMENTED_BY",
        "ISUSEDFORCOMPUTATIONOF": "IS_USED_FOR_COMPUTATION_OF",
        "ISPARTOFDIMENSION": "IS_PART_OF_DIMENSION",
        "HASFORSOURCE": "HAS_FOR_SOURCE",
        "ISSOURCEOF": "IS_SOURCE_OF",
    }

    upper = text.upper().strip()
    if upper in broken:
        return broken[upper]

    rel = re.sub(r"[^A-Za-z0-9_]", "_", text)
    rel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", rel)
    rel = rel.upper()
    rel = re.sub(r"_+", "_", rel).strip("_")

    return rel or "RELATED_TO"


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def run_neo4j(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with neo4j.session() as session:
        result = session.run(query, **(params or {}))
        rows = [dict(record) for record in result]
        result.consume()
        return rows


def read_bad_rows() -> list[dict[str, Any]]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    rows: list[dict[str, Any]] = []

    with INPUT_FILE.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            status = row.get("status")

            if status != "RELATIONSHIP_MISSING":
                continue

            src = row.get("src_node_id")
            tgt = row.get("tgt_node_id")

            if not src or not tgt:
                continue

            # Prefer the real PostgreSQL link type, not the previously broken expected_relationship.
            rel = normalize_rel(row.get("postgres_link_type") or row.get("expected_relationship"))

            row["normalized_relationship"] = rel
            rows.append(row)

    return rows


def repair_missing_relationships(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        grouped[row["normalized_relationship"]].append(row)

    summary = {}

    unresolved_file = OUTPUT_DIR / "unresolved_missing_usage_relationships_after_repair.csv"

    with unresolved_file.open("w", encoding="utf-8", newline="") as f:
        fieldnames = [
            "status",
            "src_node_id",
            "src_name_tech",
            "postgres_link_type",
            "normalized_relationship",
            "tgt_node_id",
            "tgt_name_tech",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for rel_type, rel_rows in sorted(grouped.items()):
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", rel_type):
                raise ValueError(f"Unsafe relationship type: {rel_type}")

            print(f"[REPAIR] {rel_type}: {len(rel_rows):,}")

            query = f"""
            UNWIND $rows AS row

            OPTIONAL MATCH (src_dg:DataGalaxyObject {{node_id: row.src_node_id}})
            OPTIONAL MATCH (src_usage:Usage {{usage_uuid: row.src_node_id}})
            OPTIONAL MATCH (tgt_dg:DataGalaxyObject {{node_id: row.tgt_node_id}})
            OPTIONAL MATCH (tgt_usage:Usage {{usage_uuid: row.tgt_node_id}})

            WITH row,
                 coalesce(src_dg, src_usage) AS src,
                 coalesce(tgt_dg, tgt_usage) AS tgt

            FOREACH (_ IN CASE WHEN src IS NOT NULL AND tgt IS NOT NULL THEN [1] ELSE [] END |
                MERGE (src)-[r:{rel_type}]->(tgt)
                SET r.link_type = row.postgres_link_type,
                    r.imported_from = 'link',
                    r.repaired_by = '06b_repair_missing_usage_relationships_only.py'
            )

            RETURN
                row.src_node_id AS src_node_id,
                row.src_name_tech AS src_name_tech,
                row.postgres_link_type AS postgres_link_type,
                row.normalized_relationship AS normalized_relationship,
                row.tgt_node_id AS tgt_node_id,
                row.tgt_name_tech AS tgt_name_tech,
                src IS NOT NULL AS src_exists,
                tgt IS NOT NULL AS tgt_exists
            """

            processed = 0
            unresolved = 0

            for batch_index, batch in enumerate(chunks(rel_rows, BATCH_SIZE), start=1):
                result = run_neo4j(query, {"rows": batch})
                processed += len(batch)

                for r in result:
                    if not r["src_exists"] or not r["tgt_exists"]:
                        unresolved += 1
                        writer.writerow(
                            {
                                "status": "SRC_OR_TGT_STILL_MISSING",
                                "src_node_id": r.get("src_node_id"),
                                "src_name_tech": r.get("src_name_tech"),
                                "postgres_link_type": r.get("postgres_link_type"),
                                "normalized_relationship": r.get("normalized_relationship"),
                                "tgt_node_id": r.get("tgt_node_id"),
                                "tgt_name_tech": r.get("tgt_name_tech"),
                            }
                        )

                print(
                    f"  batch {batch_index}: {processed:,}/{len(rel_rows):,} unresolved={unresolved:,}",
                    flush=True,
                )

            summary[rel_type] = {
                "input_rows": len(rel_rows),
                "processed": processed,
                "unresolved": unresolved,
            }

    return {
        "by_relationship_type": summary,
        "unresolved_file": str(unresolved_file),
    }


def post_stats() -> dict[str, Any]:
    rel_rows = run_neo4j(
        """
        MATCH ()-[r]->()
        RETURN type(r) AS relationship_type, count(r) AS count
        ORDER BY count DESC
        """
    )

    usage_rel_rows = [
        row for row in rel_rows
        if row["relationship_type"] in {
            "USES",
            "IS_USED_BY",
            "IS_USAGE_SOURCE_FOR",
            "IS_USAGE_DESTINATION_FOR",
            "HAS_FOR_SOURCE",
            "IS_SOURCE_OF",
            "IS_LINKED_TO",
            "CALLS",
            "IS_CALLED_BY",
            "IMPLEMENTS",
            "IS_IMPLEMENTED_BY",
            "GENERALIZES",
            "SPECIALIZES",
            "REGROUPS",
            "IS_PART_OF_DIMENSION",
            "HAS_FOR_UNIVERSE",
            "IS_UNIVERSE_OF",
            "IS_USED_FOR_COMPUTATION_OF",
            "IS_INPUT_OF",
            "IS_OUTPUT_OF",
        }
    ]

    return {
        "usage_relevant_relationship_distribution": usage_rel_rows,
    }


def main() -> None:
    print("=" * 80)
    print("REPAIR MISSING USAGE RELATIONSHIPS ONLY")
    print("=" * 80)
    print(f"Input:  {INPUT_FILE}")
    print(f"Output: {OUTPUT_DIR}")

    rows = read_bad_rows()

    print(f"Missing relationship rows loaded: {len(rows):,}")

    print("Distribution to repair:")
    for rel, count in Counter(row["normalized_relationship"] for row in rows).most_common():
        print(f"  {rel:<35} {count:,}")

    repair_summary = repair_missing_relationships(rows)
    stats = post_stats()

    final_summary = {
        "input_file": str(INPUT_FILE),
        "missing_relationship_rows_loaded": len(rows),
        "repair_summary": repair_summary,
        "post_stats": stats,
        "output_dir": str(OUTPUT_DIR),
    }

    summary_file = OUTPUT_DIR / "missing_usage_relationship_repair_summary.json"
    summary_file.write_text(
        json.dumps(final_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(final_summary, indent=2, ensure_ascii=False))

    neo4j.close()


if __name__ == "__main__":
    main()