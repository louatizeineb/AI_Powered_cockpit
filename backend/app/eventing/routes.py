from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.db import get_db
from app.eventing.models import DataQualityCheckResult, EventCatalogResolution, EventDLQ, EventStore, PipelineRun

router = APIRouter(prefix="/events", tags=["eventing"])
logger = logging.getLogger(__name__)


def _latest_or_empty(db: Session, model):
    try:
        return db.query(model).order_by(model.id.desc()).limit(50).all()
    except ProgrammingError as exc:
        db.rollback()
        if getattr(exc.orig, "pgcode", None) == "42P01":
            logger.warning("Optional legacy eventing table %s is not available", model.__tablename__)
            return []
        raise


@router.get("/recent")
def get_recent_events(db: Session = Depends(get_db)):
    return _latest_or_empty(db, EventStore)


@router.get("/dlq")
def get_dlq_events(db: Session = Depends(get_db)):
    return _latest_or_empty(db, EventDLQ)


@router.get("/quality-results")
def get_quality_results(db: Session = Depends(get_db)):
    return _latest_or_empty(db, DataQualityCheckResult)


@router.get("/pipeline-runs")
def get_pipeline_runs(db: Session = Depends(get_db)):
    return _latest_or_empty(db, PipelineRun)


@router.get("/resolutions")
def get_resolutions(db: Session = Depends(get_db)):
    return _latest_or_empty(db, EventCatalogResolution)
