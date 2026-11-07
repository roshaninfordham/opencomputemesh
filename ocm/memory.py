"""Client for a cognee memory service: out-of-process, for the same reason inference is.

Memory is the most sensitive object in this system. A prompt is transient; a knowledge
graph is cumulative — it accumulates everything an agent ever learned into one store.
So we never host it and never import it into the coordinator: the owner runs the
service, and this module only talks to it over HTTP.

Two rules are enforced here, not by the caller:

* the dataset is fixed at construction, so a query cannot reach another tenant's graph
  by asking for it;
* failures never carry memory content. Error strings travel to logs and receipts, which
  are not the place for what a user remembered.
"""

import re

import httpx

# Lowercase, no path separators, no traversal: the dataset name is the scope boundary.
DATASET = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class MemoryUnavailable(RuntimeError):
    """The memory service failed. Deliberately free of query text and stored content."""


def check_dataset(name: str) -> str:
    if not isinstance(name, str) or not DATASET.match(name):
        raise ValueError("dataset must be lowercase letters, digits, hyphens or underscores")
    return name


class MemoryHit:
    """One retrieved piece of memory."""

    __slots__ = ("text", "search_type")

    def __init__(self, text: str, search_type: str | None = None):
        self.text = text
        self.search_type = search_type

    def __repr__(self) -> str:  # never print memory content by accident
        return f"MemoryHit(text=<{len(self.text)} chars>, search_type={self.search_type!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, MemoryHit) and (self.text, self.search_type) == (other.text, other.search_type)


def _items(payload) -> list[dict]:
    """cognee answers a list, or an object holding one under a few known keys."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "items"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


class CogneeMemory:
    """One owner's memory, scoped to one dataset on a cognee HTTP server."""

    def __init__(self, base_url: str, dataset: str, token: str | None = None, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.dataset = check_dataset(dataset)
        options = {"base_url": self.base_url, "timeout": httpx.Timeout(timeout, connect=5), "trust_env": False}
        if token:
            options["headers"] = {"authorization": f"Bearer {token}"}
        # trust_env=False: memory content never travels through an HTTP(S)_PROXY.
        self._client = httpx.AsyncClient(**options)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def remember(self, text: str) -> None:
        """Store text and build memory. Refuses empty input instead of polluting the graph."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("refusing to store an empty memory")
        try:
            r = await self._client.post("/api/v1/remember",
                                        json={"raw_data": [text], "dataset_name": self.dataset})
        except httpx.HTTPError as e:
            raise MemoryUnavailable(f"memory service unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise MemoryUnavailable(f"remember failed with HTTP {r.status_code}")

    async def recall(self, query: str, limit: int = 5) -> list[MemoryHit]:
        """Retrieve text segments. `CHUNKS` keeps this LLM-free: with no model configured,
        the other search types return a prompt template rather than an answer."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("refusing an empty memory query")
        try:
            r = await self._client.post("/api/v1/recall", json={"query": query, "datasets": [self.dataset],
                                                                "search_type": "CHUNKS"})
        except httpx.HTTPError as e:
            raise MemoryUnavailable(f"memory service unreachable ({type(e).__name__})") from None
        if r.status_code >= 400:
            raise MemoryUnavailable(f"recall failed with HTTP {r.status_code}")
        hits = []
        for item in _items(r.json()):
            text = item.get("text") or item.get("content") or item.get("chunk") or ""
            if text:
                hits.append(MemoryHit(text=str(text), search_type=item.get("search_type")))
        return hits[:limit]
