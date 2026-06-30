from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _common import ROOT, setup_logging


LOGGER = setup_logging("migration_v2.orchestrator")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the safe inspection phases of migration_v2.")
    parser.add_argument("--export-id", required=True, help="Export identifier.")
    parser.add_argument("--export-path", required=True, help="Directory containing raw export files.")
    parser.add_argument("--contract", required=True, help="Path to mapping contract.")
    parser.add_argument(
        "--through",
        choices=["register", "profile", "drift", "mapping-plan", "preprocess", "validate", "audit"],
        default="validate",
        help="Last phase to run. Graph build and publish remain explicit gated commands.",
    )
    return parser.parse_args()


def run(script_name: str, *args: str) -> None:
    script_path = ROOT / "scripts" / "migration_v2" / script_name
    command = [sys.executable, str(script_path), *args]
    LOGGER.info("Running %s", " ".join(command))
    subprocess.run(command, cwd=str(ROOT), check=True)


def main() -> None:
    args = parse_args()
    phases = [
        ("register", "01_register_export.py", ["--export-id", args.export_id, "--export-path", args.export_path, "--contract", args.contract]),
        ("profile", "02_profile_export.py", ["--export-id", args.export_id]),
        ("drift", "03_detect_schema_drift.py", ["--export-id", args.export_id, "--contract", args.contract]),
        ("mapping-plan", "04_generate_mapping_plan.py", ["--export-id", args.export_id]),
        ("preprocess", "05_preprocess_to_staging.py", ["--export-id", args.export_id, "--contract", args.contract]),
        ("validate", "06_validate_staging.py", ["--export-id", args.export_id, "--contract", args.contract]),
        ("audit", "09_audit_and_compare.py", ["--export-id", args.export_id]),
    ]
    for phase_name, script_name, phase_args in phases:
        run(script_name, *phase_args)
        if phase_name == args.through:
            break


if __name__ == "__main__":
    main()
