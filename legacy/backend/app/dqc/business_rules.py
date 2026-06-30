from __future__ import annotations


class EventBusinessRuleError(Exception):
    """Raised when a schema-valid event violates DQC business rules."""


def validate_dataquality_business_rules(dq_result: dict) -> None:
    required_fields = [
        "application_code",
        "controlled_object_name",
        "controlled_object_type",
        "control_name",
        "execution_timestamp",
        "business_date",
        "controlled_item_count",
        "ok_count",
        "ko_count",
        "control_tool",
    ]

    missing = [
        field
        for field in required_fields
        if dq_result.get(field) is None or str(dq_result.get(field)).strip() == ""
    ]
    if missing:
        raise EventBusinessRuleError(f"Missing required DQC fields: {', '.join(missing)}")

    controlled = dq_result["controlled_item_count"]
    ok = dq_result["ok_count"]
    ko = dq_result["ko_count"]

    if controlled < 0 or ok < 0 or ko < 0:
        raise EventBusinessRuleError("Invalid DQC counts: counts must be non-negative")

    if controlled != ok + ko:
        raise EventBusinessRuleError(
            "Invalid DQC counts: expected controlled_item_count = ok_count + ko_count; "
            f"got controlled_item_count={controlled}, ok_count={ok}, ko_count={ko}"
        )
