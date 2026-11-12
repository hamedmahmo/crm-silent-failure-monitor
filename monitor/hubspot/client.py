"""A small HubSpot REST client: bearer auth, retries, batching and paced search.

The access token comes from HUBSPOT_ACCESS_TOKEN (loaded from .env). It is only ever placed in
the Authorization header: it is not logged, printed, or included in errors or repr().
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import dotenv
import httpx

from monitor import ROOT

API_VERSION = "2026-09"
BASE_URL = "https://api.hubapi.com"
TOKEN_ENV = "HUBSPOT_ACCESS_TOKEN"
PORTAL_ENV = "HUBSPOT_PORTAL_ID"
OWNER_ENV = "HUBSPOT_OWNER_ID"

UPSERT_BATCH = 100
READ_BATCH = 100
ARCHIVE_BATCH = 100
ASSOCIATION_BATCH = 2000
ASSOCIATION_READ_BATCH = 1000
LIST_PAGE = 100
SEARCH_PAGE = 200
# A batch association read reports records with no associations as an error with this
# subCategory (status 207); they are not failures.
NO_ASSOCIATIONS = "NO_ASSOCIATIONS_FOUND"
SEARCH_INTERVAL = 0.2  # HubSpot allows 5 search requests per second per account
MAX_ATTEMPTS = 5
BACKOFF_BASE = 1.0
BACKOFF_MAX = 30.0

log = logging.getLogger("monitor.hubspot")


class HubSpotError(Exception):
    """A HubSpot request failed, or the portal is not in the expected state."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ConfigError(HubSpotError):
    """A required environment variable is missing."""


def load_env() -> None:
    """Load .env from the repository root without overriding variables already set."""
    dotenv.load_dotenv(ROOT / ".env", override=False)


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set; add it to .env (see .env.example)")
    return value


def chunks(items: Sequence[Any], size: int) -> list[Sequence[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class HubSpotClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=30.0,
        )
        self._base_url = base_url
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._max_attempts = max_attempts
        self._clock = clock
        self._last_search: float | None = None
        self.last_status: int | None = None  # status of the latest response

    def __repr__(self) -> str:
        return f"HubSpotClient(base_url={self._base_url!r}, token=<hidden>)"

    def __enter__(self) -> HubSpotClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ requests

    def request(
        self,
        method: str,
        path: str,
        *,
        json: object = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        for attempt in range(1, self._max_attempts + 1):
            last = attempt == self._max_attempts
            try:
                response = self._http.request(method, path, json=json, params=params)
            except httpx.TransportError as exc:
                log.debug("%s %s -> %s", method, path, type(exc).__name__)
                if last:
                    raise HubSpotError(f"{method} {path} failed: {type(exc).__name__}") from None
                self._sleep(self._backoff(attempt))
                continue
            log.debug("%s %s -> %s", method, path, response.status_code)
            self.last_status = response.status_code
            if response.status_code == 429 or response.status_code >= 500:
                if last:
                    raise self._error(method, path, response)
                self._sleep(self._retry_delay(response, attempt))
                continue
            if response.status_code >= 400:
                raise self._error(method, path, response)
            if response.status_code == 204 or not response.content:
                return None
            return response.json()
        raise AssertionError("unreachable")

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: object) -> Any:
        return self.request("POST", path, json=json)

    def _backoff(self, attempt: int) -> float:
        return min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (attempt - 1)) + self._rng.uniform(0, 0.5)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self._backoff(attempt)

    @staticmethod
    def _error(method: str, path: str, response: httpx.Response) -> HubSpotError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        category = body.get("category") or body.get("errorType") or ""
        message = body.get("message") or response.reason_phrase
        detail = f"{category}: {message}" if category else message
        return HubSpotError(
            f"{method} {path} -> {response.status_code} {detail}", status=response.status_code
        )

    # ------------------------------------------------------------ batch helpers

    def _check_batch(self, path: str, body: Any) -> list[dict]:
        if not isinstance(body, dict):
            return []
        errors = body.get("errors") or []
        count = body.get("numErrors") or len(errors)
        if count:
            messages = "; ".join(str(e.get("message", e)) for e in errors[:3])
            raise HubSpotError(f"POST {path} reported {count} errors: {messages}".rstrip(": "))
        return list(body.get("results") or [])

    def batch_upsert(self, object_type: str, inputs: Sequence[dict]) -> list[dict]:
        path = f"/crm/objects/{API_VERSION}/{object_type}/batch/upsert"
        results: list[dict] = []
        for chunk in chunks(inputs, UPSERT_BATCH):
            results += self._check_batch(path, self.post(path, {"inputs": list(chunk)}))
        return results

    def batch_read(
        self, object_type: str, ids: Sequence[str], id_property: str, properties: Iterable[str]
    ) -> list[dict]:
        path = f"/crm/objects/{API_VERSION}/{object_type}/batch/read"
        results: list[dict] = []
        for chunk in chunks(ids, READ_BATCH):
            body = {
                "idProperty": id_property,
                "inputs": [{"id": i} for i in chunk],
                "properties": list(properties),
            }
            results += self._check_batch(path, self.post(path, body))
        return results

    def batch_archive(self, object_type: str, ids: Sequence[str]) -> int:
        path = f"/crm/objects/{API_VERSION}/{object_type}/batch/archive"
        for chunk in chunks(ids, ARCHIVE_BATCH):
            self.post(path, {"inputs": [{"id": i} for i in chunk]})
        return len(ids)

    def associate(self, from_type: str, to_type: str, pairs: Sequence[tuple[str, str]]) -> int:
        path = f"/crm/associations/{API_VERSION}/{from_type}/{to_type}/batch/associate/default"
        for chunk in chunks(pairs, ASSOCIATION_BATCH):
            inputs = [{"from": {"id": a}, "to": {"id": b}} for a, b in chunk]
            self._check_batch(path, self.post(path, {"inputs": inputs}))
        return len(pairs)

    # ------------------------------------------------------------ reads

    def list_objects(self, object_type: str, properties: Iterable[str]) -> list[dict]:
        """Every unarchived record of a type, LIST_PAGE at a time, following paging."""
        path = f"/crm/objects/{API_VERSION}/{object_type}"
        params = {"limit": str(LIST_PAGE), "properties": ",".join(properties), "archived": "false"}
        results: list[dict] = []
        while True:
            page = self.get(path, params=params) or {}
            results += page.get("results") or []
            after = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return results
            params = {**params, "after": str(after)}

    def read_associations(
        self, from_type: str, to_type: str, ids: Sequence[str]
    ) -> dict[str, list[dict]]:
        """Associations of each record, as {from ID: [{toObjectId, associationTypes}, ...]}.

        Records with no associations are left out. A record whose list is paged is asked for
        again with its `after` cursor until every page is read.
        """
        path = f"/crm/associations/{API_VERSION}/{from_type}/{to_type}/batch/read"
        found: dict[str, list[dict]] = {}
        pending: list[dict] = [{"id": str(i)} for i in ids]
        while pending:
            next_round: list[dict] = []
            for chunk in chunks(pending, ASSOCIATION_READ_BATCH):
                body = self.post(path, {"inputs": list(chunk)}) or {}
                errors = [
                    e
                    for e in body.get("errors") or []
                    if not str(e.get("subCategory", "")).endswith(NO_ASSOCIATIONS)
                ]
                if errors:
                    messages = "; ".join(str(e.get("message", e)) for e in errors[:3])
                    raise HubSpotError(f"POST {path} reported {len(errors)} errors: {messages}")
                for result in body.get("results") or []:
                    from_id = str(result["from"]["id"])
                    found.setdefault(from_id, []).extend(result.get("to") or [])
                    after = ((result.get("paging") or {}).get("next") or {}).get("after")
                    if after:
                        next_round.append({"id": from_id, "after": str(after)})
            pending = next_round
        return found

    # ------------------------------------------------------------ search

    def _pace_search(self) -> None:
        now = self._clock()
        if self._last_search is not None:
            wait = SEARCH_INTERVAL - (now - self._last_search)
            if wait > 0:
                self._sleep(wait)
                now += wait
        self._last_search = now

    def _search_page(self, object_type: str, body: dict) -> dict:
        self._pace_search()
        return self.post(f"/crm/objects/{API_VERSION}/{object_type}/search", body) or {}

    def search(
        self,
        object_type: str,
        filters: list[dict],
        properties: Iterable[str] = (),
        limit: int = SEARCH_PAGE,
    ) -> list[dict]:
        """Every record matching all filters, following paging."""
        results: list[dict] = []
        after: str | None = None
        while True:
            body: dict[str, Any] = {
                "filterGroups": [{"filters": filters}] if filters else [],
                "properties": list(properties),
                "limit": limit,
            }
            if after is not None:
                body["after"] = after
            page = self._search_page(object_type, body)
            results += page.get("results") or []
            after = (page.get("paging") or {}).get("next", {}).get("after")
            if not after:
                return results

    def count(self, object_type: str, filters: list[dict]) -> int:
        body = {"filterGroups": [{"filters": filters}] if filters else [], "limit": 1}
        return int(self._search_page(object_type, body).get("total", 0))
