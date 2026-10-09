"""Read-only client for the Honcho v3 REST API.

Only the routes in SAFE_ROUTES can be called. Several Honcho POST routes are
get-or-create (POST /v3/workspaces, .../peers, .../sessions): a mistyped ID
would silently create a new workspace or peer. The allowlist makes this
viewer incapable of writing to the server.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

SAFE_ROUTES = frozenset({
    ("GET", "/health"),
    ("POST", "/v3/workspaces/list"),
    ("GET", "/v3/workspaces/{ws}/queue/status"),
    ("POST", "/v3/workspaces/{ws}/peers/list"),
    ("GET", "/v3/workspaces/{ws}/peers/{peer}/card"),
    ("POST", "/v3/workspaces/{ws}/peers/{peer}/representation"),
    ("POST", "/v3/workspaces/{ws}/peers/{peer}/sessions"),
    ("POST", "/v3/workspaces/{ws}/peers/{peer}/chat"),
    ("POST", "/v3/workspaces/{ws}/sessions/{session}/messages/list"),
    ("POST", "/v3/workspaces/{ws}/conclusions/list"),
    ("GET", "/v3/workspaces/{ws}/conclusions/{conclusion}"),
    ("POST", "/v3/workspaces/{ws}/search"),
})
REASONING_LEVELS = ("minimal", "low", "medium", "high", "max")
CONCLUSION_LEVELS = ("explicit", "deductive", "inductive", "contradiction")
PAGE_SIZE = 100  # server maximum
REPRESENTATION_MAX = 100  # server maximum for max_conclusions; without it the server returns only 25


class HonchoError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}" if status else detail)
        self.status = status
        self.detail = detail


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "")}


def _error_detail(err: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(err.read()).get("detail", err.reason)
    except (ValueError, AttributeError):
        return str(err.reason)
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return str(detail)


class HonchoClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0, chat_timeout: float = 600.0):
        self.base_url = base_url.strip().rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self.chat_timeout = chat_timeout

    # ---- transport -----------------------------------------------------
    def _prepare(self, method: str, route: str, path: dict | None = None, query: dict | None = None,
                 body: dict | None = None, accept: str = "application/json") -> urllib.request.Request:
        if (method, route) not in SAFE_ROUTES:
            raise ValueError(f"Route is not allowlisted (viewer is read-only): {method} {route}")
        url = self.base_url + route.format(**{k: quote(str(v), safe="") for k, v in (path or {}).items()})
        query = {k: str(v).lower() if isinstance(v, bool) else v for k, v in (query or {}).items() if v is not None}
        if query:
            url += "?" + urlencode(query)
        headers = {"Accept": accept}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if method == "POST":
            data = json.dumps(body or {}).encode()
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(url, data=data, method=method, headers=headers)

    def _request(self, method: str, route: str, path: dict | None = None, query: dict | None = None,
                 body: dict | None = None, timeout: float | None = None):
        req = self._prepare(method, route, path, query, body)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            raise HonchoError(err.code, _error_detail(err)) from None
        except OSError as err:  # URLError, timeouts, refused connections
            raise HonchoError(0, f"Cannot reach {self.base_url}: {getattr(err, 'reason', err)}") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise HonchoError(0, "Server returned a non-JSON response (is the URL right?)") from None

    def _all_pages(self, route: str, path: dict, body: dict | None = None, max_items: int | None = None) -> list:
        items, page = [], 1
        while True:
            data = self._request("POST", route, path, {"page": page, "size": PAGE_SIZE}, body or {})
            items.extend(data.get("items", []))
            if page >= (data.get("pages") or 1) or (max_items is not None and len(items) >= max_items):
                return items[:max_items]
            page += 1

    # ---- reads ---------------------------------------------------------
    def health(self) -> dict:
        return self._request("GET", "/health")

    def list_workspaces(self) -> list[dict]:
        return self._all_pages("/v3/workspaces/list", {})

    def list_peers(self, ws: str) -> list[dict]:
        return self._all_pages("/v3/workspaces/{ws}/peers/list", {"ws": ws})

    def queue_status(self, ws: str) -> dict:
        return self._request("GET", "/v3/workspaces/{ws}/queue/status", {"ws": ws})

    def peer_card(self, ws: str, peer: str, target: str | None = None) -> list[str] | None:
        data = self._request("GET", "/v3/workspaces/{ws}/peers/{peer}/card",
                             {"ws": ws, "peer": peer}, {"target": target})
        return data.get("peer_card")

    def representation(self, ws: str, peer: str, target: str | None = None) -> str:
        data = self._request("POST", "/v3/workspaces/{ws}/peers/{peer}/representation",
                             {"ws": ws, "peer": peer},
                             body=_clean({"target": target, "max_conclusions": REPRESENTATION_MAX}))
        return data.get("representation") or ""

    def peer_sessions(self, ws: str, peer: str) -> list[dict]:
        return self._all_pages("/v3/workspaces/{ws}/peers/{peer}/sessions", {"ws": ws, "peer": peer})

    def messages_page(self, ws: str, session: str, page: int = 1, size: int = 50) -> dict:
        return self._request("POST", "/v3/workspaces/{ws}/sessions/{session}/messages/list",
                             {"ws": ws, "session": session}, {"page": page, "size": size}, {})

    def _conclusion_filters(self, observer, observed, level) -> dict | None:
        return _clean({"observer_id": observer, "observed_id": observed, "level": level}) or None

    def list_conclusions(self, ws: str, observer: str | None = None, observed: str | None = None,
                         level: str | None = None, max_items: int | None = None) -> list[dict]:
        return self._all_pages("/v3/workspaces/{ws}/conclusions/list", {"ws": ws},
                               {"filters": self._conclusion_filters(observer, observed, level)}, max_items)

    def count_conclusions(self, ws: str, observer: str | None = None, observed: str | None = None,
                          level: str | None = None) -> int:
        data = self._request("POST", "/v3/workspaces/{ws}/conclusions/list", {"ws": ws}, {"page": 1, "size": 1},
                             {"filters": self._conclusion_filters(observer, observed, level)})
        return int(data.get("total", 0))

    def get_conclusion(self, ws: str, conclusion_id: str) -> dict:
        return self._request("GET", "/v3/workspaces/{ws}/conclusions/{conclusion}",
                             {"ws": ws, "conclusion": conclusion_id})

    def search_messages(self, ws: str, query: str, session_id: str | None = None,
                        peer_id: str | None = None, limit: int = 5) -> list[dict]:
        filters = _clean({"session_id": session_id, "peer_id": peer_id}) or None
        return self._request("POST", "/v3/workspaces/{ws}/search", {"ws": ws},
                             body={"query": query, "limit": limit, "filters": filters})

    def chat(self, ws: str, peer: str, body: dict) -> dict:
        """Ask Honcho about a peer. ``body`` is the DialecticOptions JSON, sent as-is."""
        started = time.monotonic()
        data = self._request("POST", "/v3/workspaces/{ws}/peers/{peer}/chat",
                             {"ws": ws, "peer": peer}, body=body, timeout=self.chat_timeout)
        data["elapsed_s"] = round(time.monotonic() - started, 1)
        return data

    def chat_stream(self, ws: str, peer: str, body: dict, on_text=None) -> dict:
        """Like ``chat`` but streamed: reports when the first words arrive (``first_token_s``).

        ``on_text(text_so_far)`` is called as the answer grows. Falls back to a normal reply if the server
        ignores ``stream`` and answers with plain JSON.
        """
        started = time.monotonic()
        req = self._prepare("POST", "/v3/workspaces/{ws}/peers/{peer}/chat", {"ws": ws, "peer": peer},
                            body={**body, "stream": True}, accept="text/event-stream")
        parts: list[str] = []
        first = evidence = None
        try:
            with urllib.request.urlopen(req, timeout=self.chat_timeout) as resp:
                if "json" in resp.headers.get_content_type():
                    data = json.loads(resp.read())
                    data["elapsed_s"] = round(time.monotonic() - started, 2)
                    return data
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        chunk = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    text = (chunk.get("delta") or {}).get("content")
                    if text:
                        if first is None:
                            first = round(time.monotonic() - started, 2)
                        parts.append(text)
                        if on_text:
                            on_text("".join(parts))
                    if chunk.get("evidence"):
                        evidence = chunk["evidence"]
                    if chunk.get("done"):
                        break
        except urllib.error.HTTPError as err:
            raise HonchoError(err.code, _error_detail(err)) from None
        except OSError as err:
            raise HonchoError(0, f"Cannot reach {self.base_url}: {getattr(err, 'reason', err)}") from None
        return {"content": "".join(parts), "evidence": evidence, "first_token_s": first,
                "elapsed_s": round(time.monotonic() - started, 2)}
