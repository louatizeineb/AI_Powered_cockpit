from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any


class DQCEvent(BaseModel):
    id: str | int | None = None
    applicationcode: str | None = None
    controlledobjectname: str | None = None
    controlledobjecttype: str | None = None
    controlledsourcename: str | None = None
    businesstermname: str | None = None
    controlname: str | None = None
    qualitydimension: str | None = None
    acceptancethreshold: float | str | None = None
    executiontimestamp: str | None = None
    businessdate: str | None = None
    controlleditemcount: int | str | None = None
    okcount: int | str | None = None
    kocount: int | str | None = None
    controltool: str | None = None
    cdqprofile: str | None = None
    controllink: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class UploadResponse(BaseModel):
    run_id: str
    received: int
    processed: int
    matched: int
    review: int
    dlq: int
