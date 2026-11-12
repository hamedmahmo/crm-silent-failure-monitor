"""Import the stdlib-only scripts in data/ (the generator and the day-2 simulator)."""

from __future__ import annotations

import sys
from types import ModuleType

from monitor import ROOT

DATA_DIR = ROOT / "data"


def _ensure_path() -> None:
    if str(DATA_DIR) not in sys.path:
        sys.path.insert(0, str(DATA_DIR))


def generator() -> ModuleType:
    _ensure_path()
    import generate

    return generate


def simulator() -> ModuleType:
    _ensure_path()
    import simulate_day2

    return simulate_day2
