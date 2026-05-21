from __future__ import annotations
from typing import Any
from app.common.text import normalize_text


def parse_controlled_object(applicationcode: Any, controlledobjectname: Any, controlledsourcename: Any = None) -> dict:
    app_code = normalize_text(applicationcode)
    app_code = app_code.upper() if app_code else None
    source_name = normalize_text(controlledsourcename)

    raw = str(controlledobjectname or "").strip().replace("[", "").replace("]", "")
    clean = normalize_text(raw) or ""

    # Preserve dot split before aggressive normalization.
    raw_dot_parts = [normalize_text(p) for p in raw.split(".") if normalize_text(p)]

    if len(raw_dot_parts) >= 2:
        structure_name = raw_dot_parts[0]
        field_name = raw_dot_parts[-1]
        target_level = "Field"
    else:
        structure_name = clean or source_name
        field_name = None
        target_level = "Structure"

    return {
        "application_code_norm": app_code,
        "controlled_source_name_norm": source_name,
        "controlled_structure_name": structure_name,
        "controlled_field_name": field_name,
        "target_level": target_level,
    }
