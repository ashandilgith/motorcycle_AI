"""One place to create HTTP clients, so tests can swap in a mock transport."""
from __future__ import annotations

import httpx

_transport: httpx.BaseTransport | None = None
USER_AGENT = "motorcycle_AI/0.1 (+https://github.com/ashandilgith/motorcycle_AI)"


def client(timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=_transport, headers={"User-Agent": USER_AGENT})


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests: set_transport(httpx.MockTransport(handler)). Production: leave as None."""
    global _transport
    _transport = transport
