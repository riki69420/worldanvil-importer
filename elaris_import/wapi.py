"""Minimal World Anvil Boromir (API v2) client.

Endpoints and auth headers come from the published OpenAPI spec:
https://www.worldanvil.com/api/external/boromir/swagger-documentation

Creating an application key requires a Worldbuilder's Guild membership above
Grandmaster rank; the key and the per-user auth token are both sent as headers
on every call.
"""

from __future__ import annotations

import time
from typing import Any

import requests

BASE_URL = "https://www.worldanvil.com/api/external/boromir"
USER_AGENT = "elaris-import (https://github.com/riki69420/claude, 1.0.0)"


class WorldAnvilError(RuntimeError):
    def __init__(self, method: str, path: str, response: requests.Response):
        self.status = response.status_code
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:500]
        super().__init__(f"{method} {path} -> {response.status_code}: {detail}")


class WorldAnvil:
    def __init__(self, application_key: str, auth_token: str, *, delay: float = 0.4):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "x-application-key": application_key,
                "x-auth-token": auth_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        # World Anvil publishes no rate limit; pace calls so a 139-article
        # import does not look like a burst.
        self.delay = delay

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        for attempt in range(4):
            response = self.session.request(method, url, timeout=60, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    time.sleep(2**attempt)
                    continue
            if not response.ok:
                raise WorldAnvilError(method, path, response)
            time.sleep(self.delay)
            return response.json()
        raise WorldAnvilError(method, path, response)

    # -- identity ---------------------------------------------------------
    def identity(self) -> dict[str, Any]:
        """The authenticated user. Use it to check credentials before a run."""
        return self._request("GET", "/identity")

    def worlds(self, user_id: str) -> list[dict[str, Any]]:
        """Every world the user owns.

        The parent id goes in the query string and paging in the body — the
        convention every Boromir list endpoint follows.
        """
        return self._collection("/user/worlds", user_id)

    def _collection(self, path: str, parent_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            result = self._request(
                "POST", path, params={"id": parent_id},
                json={"limit": 50, "offset": offset},
            )
            page = result.get("entities") or []
            out.extend(page)
            if len(page) < 50:
                return out
            offset += 50

    # -- articles ---------------------------------------------------------
    def create_article(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/article", json=payload)

    def update_article(self, article_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "PATCH", "/article", params={"id": article_id}, json=payload
        )

    def world_articles(self, world_id: str) -> list[dict[str, Any]]:
        """Every article in a world, paged 50 at a time."""
        return self._collection("/world/articles", world_id)

    # -- maps -------------------------------------------------------------
    def create_map(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/map", json=payload)

    def create_marker(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/marker", json=payload)

    def create_marker_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/markergroup", json=payload)
