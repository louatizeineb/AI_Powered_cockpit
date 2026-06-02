from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.eventing.models import DataQualityCheckResult, EventCatalogResolution, EventDLQ, EventStore, PipelineRun

router = APIRouter(prefix="/events", tags=["eventing"])


@router.get("/recent")
def get_recent_events(db: Session = Depends(get_db)):
    return db.query(EventStore).order_by(EventStore.id.desc()).limit(50).all()


@router.get("/dlq")
def get_dlq_events(db: Session = Depends(get_db)):
    return db.query(EventDLQ).order_by(EventDLQ.id.desc()).limit(50).all()


@router.get("/quality-results")
def get_quality_results(db: Session = Depends(get_db)):
    return db.query(DataQualityCheckResult).order_by(DataQualityCheckResult.id.desc()).limit(50).all()


@router.get("/pipeline-runs")
def get_pipeline_runs(db: Session = Depends(get_db)):
    return db.query(PipelineRun).order_by(PipelineRun.id.desc()).limit(50).all()


@router.get("/resolutions")
def get_resolutions(db: Session = Depends(get_db)):
    return db.query(EventCatalogResolution).order_by(EventCatalogResolution.id.desc()).limit(50).all()
