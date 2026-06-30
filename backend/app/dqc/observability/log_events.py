from __future__ import annotations

import json
from typing import Any

from app.dqc.config import DQC_DLQ_LOG_FILE, DQC_LOG_DIR


def write_dlq_log(document: dict[str, Any]) -> None:
    DQC_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with DQC_DLQ_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(document, ensure_ascii=False) + "\n")
