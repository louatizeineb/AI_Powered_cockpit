from __future__ import annotations
import uuid
from app.dqc.resolution.validator import validate_schema, validate_counts
from app.dqc.resolution.normalizer import normalize_event
from app.dqc.resolution.matcher import generate_candidates
from app.dqc.resolution.scoring import confidence
from app.dqc.resolution.llm_analyzer import explain_unresolved
from app.dqc.resolution import repository as repo
from app.config import get_settings
from app.observability.logger import log_event

settings = get_settings()


def process_event(event: dict, source_system: str = "api", run_id: str | None = None) -> dict:
    run_id = run_id or str(uuid.uuid4())
    log_event(run_id, "DQC_RECEIVED", "INFO", "DQC event received")
    raw_id = repo.save_raw(event, run_id=run_id, source_system=source_system)

    schema = validate_schema(event)
    if not schema.valid:
        dlq_id = repo.save_dlq(run_id, raw_id, None, "SCHEMA_VALIDATION", schema.reason, schema.details or {})
        log_event(run_id, "SCHEMA_VALIDATION", "ERROR", schema.reason or "schema invalid", schema.details)
        return {"status": "DLQ", "run_id": run_id, "dlq_id": dlq_id, "reason": schema.reason}

    counts = validate_counts(event)
    if not counts.valid:
        dlq_id = repo.save_dlq(run_id, raw_id, None, "BUSINESS_VALIDATION", counts.reason, counts.details or {})
        log_event(run_id, "BUSINESS_VALIDATION", "ERROR", counts.reason or "business invalid", counts.details)
        return {"status": "DLQ", "run_id": run_id, "dlq_id": dlq_id, "reason": counts.reason}

    normalized = normalize_event(event, source_system=source_system)
    normalized_id = repo.save_normalized(raw_id, normalized)
    log_event(run_id, "NORMALIZATION", "INFO", "DQC event normalized", {"normalized_id": normalized_id})

    candidates = generate_candidates(normalized, use_embeddings=True)
    if candidates:
        repo.save_candidates(normalized_id, candidates)

    if not candidates:
        analysis = explain_unresolved(normalized, [], "NO_CATALOG_CANDIDATE")
        dlq_id = repo.save_dlq(run_id, raw_id, normalized_id, "MATCHING", "NO_CATALOG_CANDIDATE", {"normalized": normalized}, analysis)
        return {"status": "DLQ", "run_id": run_id, "dlq_id": dlq_id, "reason": "NO_CATALOG_CANDIDATE"}

    best = candidates[0]
    conf = confidence(best.get("match_score", 0), settings.dqc_high_confidence, settings.dqc_medium_confidence)

    if conf == "HIGH":
        resolved_id = repo.save_resolved(normalized_id, best, "HIGH", False)
        log_event(run_id, "MATCHING", "INFO", "High-confidence DQC match", {"resolved_id": resolved_id, "score": best.get("match_score")})
        return {"status": "MATCHED", "run_id": run_id, "resolved_id": resolved_id, "confidence": conf, "best": best}

    if conf == "MEDIUM":
        resolved_id = repo.save_resolved(normalized_id, best, "MEDIUM", True)
        log_event(run_id, "HUMAN_REVIEW", "WARN", "Medium-confidence match requires human validation", {"resolved_id": resolved_id, "score": best.get("match_score")})
        return {"status": "MATCHED_WITH_REVIEW", "run_id": run_id, "resolved_id": resolved_id, "confidence": conf, "best": best}

    analysis = explain_unresolved(normalized, candidates, "LOW_CONFIDENCE_MATCH")
    dlq_id = repo.save_dlq(run_id, raw_id, normalized_id, "MATCHING", "LOW_CONFIDENCE_MATCH", {"best_score": best.get("match_score"), "top_candidates": candidates[:5]}, analysis)
    log_event(run_id, "MATCHING", "ERROR", "Low-confidence match sent to DLQ", {"dlq_id": dlq_id})
    return {"status": "DLQ", "run_id": run_id, "dlq_id": dlq_id, "reason": "LOW_CONFIDENCE_MATCH", "best": best}


def process_many(events: list[dict], source_system: str = "batch") -> dict:
    run_id = str(uuid.uuid4())
    stats = {"run_id": run_id, "received": len(events), "processed": 0, "matched": 0, "review": 0, "dlq": 0}
    for event in events:
        result = process_event(event, source_system=source_system, run_id=run_id)
        stats["processed"] += 1
        if result["status"] == "MATCHED":
            stats["matched"] += 1
        elif result["status"] == "MATCHED_WITH_REVIEW":
            stats["review"] += 1
        else:
            stats["dlq"] += 1
    return stats
