"""Shared fixtures: Northwind generator output written once per test session."""

from __future__ import annotations

from pathlib import Path

import pytest

import generate


def write_northwind(out: Path, defects: bool, seed: int = 42, scale: float = 1.0) -> Path:
    tables, manifest = generate.build(seed=seed, scale=scale, defects=defects)
    generate.write_output(out, tables, manifest, seed, scale)
    return out


@pytest.fixture(scope="session")
def northwind_clean_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_northwind(tmp_path_factory.mktemp("northwind_clean"), defects=False)


@pytest.fixture(scope="session")
def northwind_defect_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_northwind(tmp_path_factory.mktemp("northwind_defects"), defects=True)
