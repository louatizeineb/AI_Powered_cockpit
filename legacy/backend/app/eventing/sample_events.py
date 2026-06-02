from __future__ import annotations
# This file contains sample event payloads for testing and documentation purposes.

SAMPLE_DATAQUALITY_EVENT = {
    "payload": {
        "entity": {
            "type": "DataQualityCheckResult",
            "idRef": "dq-test-001",
            "data": {
                "applicationCode": "ABE",
                "controlledObjectName": "CUSTOMER",
                "controlledObjectType": "TABLE",
                "controlledSourceName": "ABE",
                "businessTermName": "Client",
                "controlName": "null_check_customer_id",
                "qualityDimension": "completeness",
                "acceptanceThreshold": 95.0,
                "executionTimestamp": "2026-05-11T10:00:00Z",
                "businessDate": "2026-05-11",
                "controlledItemCount": 1000,
                "okCount": 920,
                "koCount": 80,
                "controlTool": "IcebergProfiler",
                "comment": "Synthetic test event",
                "errors": [],
            },
            "links": [],
        }
    },
    "metadata": {"eventId": "evt-dq-test-001"},
    "origin": None,
}

SAMPLE_PIPELINE_EVENT = {
    "headers": {
        "pipelineType": "airflow",
        "eventStatus": "completed",
        "eventType": "pipelineEnd",
        "timestamp": "2026-05-11T10:05:00Z",
        "correlationId": "run-test-001",
    },
    "payload": {
        "pipelineName": "load_customer_table",
        "status": "success",
        "startTime": "2026-05-11T10:00:00Z",
        "endTime": "2026-05-11T10:05:00Z",
        "duration": "PT5M",
        "source": {"database": "iceberg_dwh", "table": "CUSTOMER"},
        "details": {"dag_id": "load_customer_table", "task_count": 8},
    },
    "metadata": {
        "environment": "dev",
        "version": "v1",
        "stepId": "pipeline-end",
        "sourcePipeline": "airflow",
        "targetPipeline": "lineage-cockpit",
        "severity": "info",
    },
}

BAD_DATAQUALITY_EVENT = {
    "payload": {
        "entity": {
            "type": "DataQualityCheckResult",
            "idRef": "dq-bad-001",
            "data": {
                # missing applicationCode, controlName, etc.
                "businessDate": "bad-date",
                "controlledItemCount": "not-an-integer",
            },
        }
    },
    "metadata": {"eventId": "evt-dq-bad-001"},
}
