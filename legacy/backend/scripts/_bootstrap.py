from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent


def _load_env_file(path: Path, *, override: bool = False) -> None:
    if load_dotenv is not None:
        load_dotenv(path, override=override)
        return

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if override or key not in os.environ:
            os.environ[key] = value


def bootstrap_backend() -> None:
    for path in (ROOT_DIR, BACKEND_DIR):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)

    _load_env_file(BACKEND_DIR / ".env")
    _load_env_file(BACKEND_DIR / ".env.eventing", override=True)
