from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.migration_v2.governance.service import provenance
from app.migration_v2.routes import migration_engine

router = APIRouter(prefix="/graphrag", tags=["GraphRAG"])


class GraphRAGRequest(BaseModel):
    query: str
    app_code: str | None = None
    target_level: str | None = None
    limit: int = 10
    export_id: str | None = None
    subject: str | None = None


class GovernanceGraphRAGRequest(BaseModel):
    export_id: str
    query: str
    subject: str | None = None
    limit: int = 10


@router.get("/health")
def graphrag_health():
    return {
        "status": "ok",
        "module": "graphrag"
    }


@router.post("/retrieve")
def retrieve_context(payload: GraphRAGRequest):
    """
    Temporary safe endpoint.

    Later, this will call the real GraphRAG retriever.
    For now, it keeps the backend bootable.
    """
    if payload.export_id:
        return governance_retrieve(
            GovernanceGraphRAGRequest(
                export_id=payload.export_id,
                query=payload.query,
                subject=payload.subject,
                limit=payload.limit,
            )
        )
    return {
        "status": "ok",
        "query": payload.query,
        "app_code": payload.app_code,
        "target_level": payload.target_level,
        "limit": payload.limit,
        "message": "GraphRAG route is active. Retriever implementation can be connected later."
    }


@router.post("/governance/retrieve")
def governance_retrieve(payload: GovernanceGraphRAGRequest):
    try:
        events = provenance(
            migration_engine(), payload.export_id, subject=payload.subject, limit=min(payload.limit, 50)
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Governance provenance is unavailable: {exc}") from exc

    if not events:
        answer = "No governance evidence matched this export and subject. The item may not have entered the decision workflow yet."
    else:
        event_counts: dict[str, int] = {}
        for event in events:
            event_type = str(event.get("event_type") or "event")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        latest = events[0]
        summary = ", ".join(f"{count} {name}" for name, count in sorted(event_counts.items()))
        answer = (
            f"Found {len(events)} provenance events for {payload.export_id}: {summary}. "
            f"The latest event is {latest.get('event_type')} with status {latest.get('status')} "
            f"by {latest.get('actor') or 'an automated policy'} at {latest.get('occurred_at')}. "
            "Review the cited evidence before changing publication policy."
        )
    return {
        "status": "ok",
        "query": payload.query,
        "export_id": payload.export_id,
        "subject": payload.subject,
        "answer": answer,
        "citations": [
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "subject_id": event["subject_id"],
                "occurred_at": event["occurred_at"],
                "payload": event["payload"],
            }
            for event in events
        ],
    }
