from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft7Validator, Draft202012Validator, FormatChecker

from backend.app.eventing.schema_loader import load_dataquality_schema, load_pipeline_schema


class EventValidationError(Exception):
    pass


def _format_errors(errors) -> str:
    return "; ".join(
        f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}"
        for error in errors
    )


def _relax_external_refs(schema: dict) -> dict:
    """Allow validation without having BPI's external bpi-standard-metadata schema locally."""
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


def validate_pipeline_event(event: dict) -> None:
    schema = load_pipeline_schema()
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(event), key=lambda e: list(e.path))
    if errors:
        raise EventValidationError(_format_errors(errors))
