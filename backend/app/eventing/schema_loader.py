from __future__ import annotations

import json
from pathlib import Path

from backend.app.eventing.config import SCHEMA_DIR


def load_json_schema(filename: str) -> dict:
    path = Path(SCHEMA_DIR) / filename
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_dataquality_schema() -> dict:
    return load_json_schema("dataqualitycheckresult_v2.json")


def load_pipeline_schema() -> dict:
    return load_json_schema("pipeline_event_v1.json")
