from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"


def bootstrap_eventing() -> None:
    for path in (ROOT_DIR, BACKEND_DIR):
        sys.path.insert(0, str(path))

    load_dotenv(BACKEND_DIR / ".env")
    load_dotenv(BACKEND_DIR / ".env.eventing", override=True)
