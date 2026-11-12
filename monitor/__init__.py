"""CRM Silent-Failure Monitor: snapshot CRM data, run SQL checks, report what changed."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS_DIR = ROOT / "checks"
