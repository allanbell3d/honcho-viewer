"""In-process fake of the Honcho v3 read routes, for tests and offline previews.

Run directly to serve it on port 8765:  python tests/fake_honcho.py
Token: "test-token".
"""
from __future__ import annotations

import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN = "test-token"
LEVELS = ("explicit", "deductive", "inductive", "contradiction")
PEERS = ("alice", "agent", "user@home/x")


def _ts(i: int) -> str:
    return f"2026-10-0{1 + i % 5}T10:{i % 60:02d}:00Z"


def build_workspace(ws_id: str, model: str, n_conclusions: int = 6) -> dict:
    """Same source conversation in every workspace; conclusions differ by model."""
    sessions = {
        "s1": [
            ("alice", "I live in Lisbon and I love <b>sailing</b>."),
            ("agent", "Noted. How long have you been sailing?"),
            ("alice", "About ten years, mostly on weekends."),
        ],
        "s2": [("alice", f"filler message {i}") for i in range(120)],
    }
    messages = {}
    for sid, rows in sessions.items():
        messages[sid] = [
            {
                "id": f"{ws_id}-{sid}-m{i}",
                "content": content,
                "peer_id": peer,
                "session_id": sid,
                "workspace_id": ws_id,
                "metadata": {},
                "created_at": _ts(i),
                "token_count": len(content.split()),
            }
            for i, (peer, content) in enumerate(rows)
        ]
    conclusions = [
        {
            "id": f"{ws_id}-c{i}",
            "content": f"[{model}] Alice fact {i}: likes sailing",
            "observer_id": "alice",
            "observed_id": "alice",
            "session_id": "s1",
            "level": LEVELS[i % len(LEVELS)],
            "source_ids": [] if i % len(LEVELS) == 0 else [f"{ws_id}-c0"],
            "times_derived": 1 + i % 3,
            "created_at": _ts(i),
        }
        for i in range(n_conclusions)
    ]
    conclusions.append(
        {
            "id": f"{ws_id}-h0",
            "content": f"[{model}] Agent thinks Alice lives in Lisbon",
            "observer_id": "agent",
            "observed_id": "alice",
            "session_id": "s1",
            "level": "explicit",
            "source_ids": [],
            "times_derived": 1,
            "created_at": _ts(0),
        }
    )
    if n_conclusions <= 10:  # "observe others": agent keeps its own identical copy of every fact about alice
        conclusions += [{**c, "id": f"{ws_id}-d{i}", "observer_id": "agent"}
                        for i, c in enumerate(conclusions[:n_conclusions])]
    return {
        "model": model,
        "workspace": {"id": ws_id, "metadata": {"model": model}, "configuration": {}, "created_at": _ts(0)},
        "peers": [
            {"id": p, "workspace_id": ws_id, "created_at": _ts(0), "metadata": {}, "configuration": {}}
            for p in PEERS
        ],
        "sessions": {
            "s1": {"peers": ["alice", "agent"], "messages": messages["s1"]},
            "s2": {"peers": ["alice"], "messages": messages["s2"]},
        },
        "conclusions": conclusions,
    }


class FakeHoncho:
    def __init__(self) -> None:
        self.workspaces = {
            "test-qwen": build_workspace("test-qwen", "qwen3-32b"),
            "test-llama": build_workspace("test-llama", "llama3-70b"),
            "big": build_workspace("big", "bulk", n_conclusions=130),
        }
        self.requests: list[dict] = []
        self.unexpected: list[tuple[str, str]] = []
        self._server: ThreadingHTTPServer | None = None

    def start(self, port: int = 0) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self._server.fake = self  # type: ignore[attr-defined]
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def last(self, suffix: str) -> dict:
        """Most recent recorded request whose path ends with ``suffix``."""
        for req in reversed(self.requests):
            if req["path"].endswith(suffix):
                return req
        raise AssertionError(f"no request ending with {suffix!r}")

    # ---- routing -------------------------------------------------------
    def route(self, method: str, seg: list[str], query: dict, body: dict | None, auth: str | None):
        if method == "GET" and seg == ["health"]:
            return 200, {"status": "ok"}
        if auth != f"Bearer {TOKEN}":
            return 401, {"detail": "Invalid access token"}
        body = body or {}
        if seg[:2] == ["v3", "workspaces"] and len(seg) > 2 and seg[2] != "list":
            ws = self.workspaces.get(seg[2])
            if ws is None:
                return 404, {"detail": f"Workspace {seg[2]} not found"}
        match (method, seg):
            case ("POST", ["v3", "workspaces", "list"]):
                return self._page([w["workspace"] for w in self.workspaces.values()], query)
            case ("POST", ["v3", "workspaces", _, "peers", "list"]):
                return self._page(ws["peers"], query)
            case ("GET", ["v3", "workspaces", _, "queue", "status"]):
                return 200, {"total_work_units": 3, "completed_work_units": 2,
                             "in_progress_work_units": 1, "pending_work_units": 0, "sessions": None}
            case ("GET", ["v3", "workspaces", _, "peers", peer, "card"]):
                about = query.get("target", peer)
                return 200, {"peer_card": [f"{about} lives in Lisbon", f"card by {ws['model']}"]}
            case ("POST", ["v3", "workspaces", _, "peers", peer, "representation"]):
                return 200, {"representation": f"{ws['model']} representation of {body.get('target') or peer}"}
            case ("POST", ["v3", "workspaces", _, "peers", peer, "sessions"]):
                rows = [
                    {"id": sid, "is_active": True, "workspace_id": seg[2], "metadata": {},
                     "configuration": {}, "created_at": _ts(0)}
                    for sid, s in ws["sessions"].items() if peer in s["peers"]
                ]
                return self._page(rows, query)
            case ("POST", ["v3", "workspaces", _, "sessions", sid, "messages", "list"]):
                return self._page(ws["sessions"][sid]["messages"], query)
            case ("POST", ["v3", "workspaces", _, "conclusions", "list"]):
                filters = body.get("filters") or {}
                bad = set(filters) - {"observer_id", "observed_id", "level", "session_id", "created_at"}
                if bad:
                    return 422, {"detail": [{"msg": f"bad filter {sorted(bad)}"}]}
                window = filters.pop("created_at", None) or {}
                rows = [c for c in ws["conclusions"] if all(c.get(k) == v for k, v in filters.items())
                        and window.get("gte", "") <= c["created_at"] <= window.get("lte", "￿")]
                return self._page(rows, query)
            case ("POST", ["v3", "workspaces", _, "conclusions", "query"]):
                f = body.get("filters") or {}
                if not f.get("observer_id") or not f.get("observed_id"):
                    return 400, {"detail": "observer and observed must be specified"}
                rows = [c for c in ws["conclusions"]
                        if c["observer_id"] == f["observer_id"] and c["observed_id"] == f["observed_id"]]
                return 200, rows[: body.get("top_k", 10)]
            case ("GET", ["v3", "workspaces", _, "conclusions", cid]):
                for c in ws["conclusions"]:
                    if c["id"] == cid:
                        return 200, c
                return 404, {"detail": "Conclusion not found"}
            case ("POST", ["v3", "workspaces", _, "search"]):
                f = body.get("filters") or {}
                words = {w.lower() for w in body.get("query", "").split()}
                rows = [
                    m for s in ws["sessions"].values() for m in s["messages"]
                    if all(m.get(k) == v for k, v in f.items())
                    and words & {w.lower().strip(".,<>/b") for w in m["content"].split()}
                ]
                return 200, rows[: body.get("limit", 10)]
            case ("POST", ["v3", "workspaces", _, "peers", peer, "chat"]):
                evidence = None
                if body.get("include_evidence"):
                    evidence = {"conclusions": ws["conclusions"][:2],
                                "messages": [{"id": "m1", "session_id": "s1", "peer_id": peer,
                                              "created_at": _ts(0)}],
                                "tool_calls": [{"tool_name": "search_memory", "tool_input": {"q": "x"}}],
                                "reasoning_trace_id": None}
                text = f"[{ws['model']}] answer to: {body.get('query')}"
                if body.get("stream"):  # Honcho's SSE format: delta chunks, then a terminal chunk with evidence
                    words = text.split(" ")
                    chunks = [{"delta": {"content": w + " "}, "done": False} for w in words]
                    chunks.append({"delta": {}, "done": True, "evidence": evidence})
                    return 200, ("sse", chunks)
                return 200, {"content": text, "evidence": evidence}
        self.unexpected.append((method, "/".join(seg)))
        return 404, {"detail": "Not Found"}

    @staticmethod
    def _page(items: list, query: dict):
        page, size = int(query.get("page", 1)), int(query.get("size", 50))
        if size > 100:
            return 422, {"detail": [{"msg": "size must be <= 100"}]}
        if query.get("reverse") == "true":
            items = list(reversed(items))
        start = (page - 1) * size
        return 200, {"items": items[start:start + size], "total": len(items), "page": page,
                     "size": size, "pages": max(1, math.ceil(len(items) / size))}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        fake: FakeHoncho = self.server.fake  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        # Split before unquoting so an encoded "/" (%2F) stays inside its segment.
        segments = [unquote(s) for s in parts.path.strip("/").split("/")]
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        auth = self.headers.get("Authorization")
        fake.requests.append({"method": method, "path": parts.path, "segments": segments,
                              "query": query, "body": body, "auth": auth})
        status, payload = fake.route(method, segments, query, body, auth)
        if isinstance(payload, tuple) and payload[0] == "sse":
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in payload[1]:
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
                time.sleep(0.02)
            return
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    fake = FakeHoncho()
    print("Fake Honcho on", fake.start(8765), "token:", TOKEN)
    threading.Event().wait()
