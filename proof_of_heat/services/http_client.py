from __future__ import annotations

import httpx

USER_AGENT = "proof-of-heat/0.1 (+https://github.com/iaon/proof-of-heat)"


def create_http_client(timeout_s: float = 10.0) -> httpx.Client:
    """Create an outbound API client with the application's identity."""
    return httpx.Client(timeout=timeout_s, headers={"User-Agent": USER_AGENT})
