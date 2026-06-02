from __future__ import annotations

import random
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

APP_CODES = ["ABE", "MKD", "IAE", "API", "AAS"]
OBJECT_NAMES = ["CUSTOMER", "DATE_ECHEANCE", "ID_CLIENT", "CONTRACT", "ACCOUNT"]
OBJECT_TYPES = ["FIELD", "TABLE", "COLUMN", "STRUCTURE"]
QUALITY_DIMENSIONS = ["completeness", "validity", "consistency", "uniqueness"]


def make_valid_event(*, app_code: str | None = None, object_name: str | None = None, object_type: str | None = None) -> dict:
    total = random.randint(100, 5000)
    ko = random.randint(0, max(1, total // 10))
    ok = total - ko
    event_id = f"dq-{uuid4()}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    return {
        "payload": {
            "entity": {
                "type": "DataQualityCheckResult",
                "idRef": event_id,
                "data": {
                    "applicationCode": app_code or random.choice(APP_CODES),
                    "controlledObjectName": object_name or random.choice(OBJECT_NAMES),
                    "controlledObjectType": object_type or random.choice(OBJECT_TYPES),
                    "controlledSourceName": app_code or random.choice(APP_CODES),
                    "businessTermName": random.choice(["Client", "Contrat", "Compte", None]),
                    "controlName": f"auto_check_{random.choice(QUALITY_DIMENSIONS)}",
                    "qualityDimension": random.choice(QUALITY_DIMENSIONS),
                    "acceptanceThreshold": random.choice([80.0, 90.0, 95.0, 99.0]),
                    "executionTimestamp": now,
                    "businessDate": now[:10],
                    "controlledItemCount": total,
                    "okCount": ok,
                    "koCount": ko,
                    "controlTool": "SyntheticDQCGenerator",
                    "comment": "Synthetic generated DQC event",
                    "errors": [],
                },
                "links": [],
            }
        },
        "metadata": {"eventId": event_id},
        "origin": None,
    }


def make_missing_app_code_event() -> dict:
    event = make_valid_event()
    event["payload"]["entity"]["data"].pop("applicationCode", None)
    return event


def make_missing_object_name_event() -> dict:
    event = make_valid_event()
    event["payload"]["entity"]["data"].pop("controlledObjectName", None)
    return event


def make_bad_counts_event() -> dict:
    event = make_valid_event()
    data = event["payload"]["entity"]["data"]
    data["controlledItemCount"] = 1000
    data["okCount"] = 900
    data["koCount"] = 50
    return event


def make_bad_integer_event() -> dict:
    event = make_valid_event()
    event["payload"]["entity"]["data"]["controlledItemCount"] = "not-an-integer"
    return event


def make_unknown_app_code_event() -> dict:
    return make_valid_event(app_code="UNKNOWN_APP", object_name="UNKNOWN_OBJECT")


def make_event_batch(size: int = 20) -> list[dict]:
    factories = [
        make_valid_event,
        make_unknown_app_code_event,
        make_missing_app_code_event,
        make_missing_object_name_event,
        make_bad_counts_event,
        make_bad_integer_event,
    ]
    return [random.choice(factories)() for _ in range(size)]
