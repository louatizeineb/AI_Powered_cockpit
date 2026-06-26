from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_contract(path: Path) -> dict[str, Any]:
    """Load the v2 mapping contract.

    The initial contract is JSON-shaped YAML so it can run without adding PyYAML.
    """

    return json.loads(path.read_text(encoding="utf-8"))
