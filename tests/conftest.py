"""Shared fixtures: Northwind generator output written once per test session."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import generate

HUBSPOT_ENV = ("HUBSPOT_ACCESS_TOKEN", "HUBSPOT_PORTAL_ID", "HUBSPOT_OWNER_ID")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would send a real HTTP request. MockTransport is unaffected."""

    def refuse(self, request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"network access in tests: {request.method} {request.url.host}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never load the real .env in tests, and start every test without HubSpot settings."""
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    for name in HUBSPOT_ENV:
        monkeypatch.delenv(name, raising=False)


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
