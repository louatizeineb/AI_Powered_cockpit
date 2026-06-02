from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from backend.app.dqc.schema_loader import load_dataquality_schema


class EventValidationError(Exception):
    """Raised when an incoming event does not conform to the JSON schema."""


def _format_errors(errors) -> str:
    return "; ".join(
        f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
        for error in errors
    )


def _relax_external_refs(schema: dict) -> dict:
    """Allows local validation even when external enterprise metadata refs are unavailable."""
    schema = deepcopy(schema)
    properties = schema.get("properties", {})
    if "metadata" in properties and "$ref" in properties["metadata"]:
        properties["metadata"] = {"type": "object"}
    return schema


def validate_dataquality_event(event: dict) -> None:
    schema = _relax_external_refs(load_dataquality_schema())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(event), key=lambda e: list(e.path))
    if errors:
        raise EventValidationError(_format_errors(errors))
