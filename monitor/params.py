"""Business-rule parameters shared by all checks (checks/params.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from monitor import CHECKS_DIR


@dataclass(frozen=True)
class Params:
    source_required_from: date
    stale_stage_days: int
    max_deal_amount: int
    open_stages: tuple[str, ...]
    closed_stages: tuple[str, ...]


def load_params(path: Path = CHECKS_DIR / "params.yaml") -> Params:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cutoff = data["source_required_from"]
    if not isinstance(cutoff, date):
        raise ValueError(f"{path}: source_required_from must be a date, got {cutoff!r}")
    return Params(
        source_required_from=cutoff,
        stale_stage_days=int(data["stale_stage_days"]),
        max_deal_amount=int(data["max_deal_amount"]),
        open_stages=tuple(data["deal_stages"]["open"]),
        closed_stages=tuple(data["deal_stages"]["closed"]),
    )
