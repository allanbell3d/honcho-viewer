"""Live telemetry from Honcho: a passive HTTP receiver, an in-memory store and an on-disk log.

Honcho can POST CloudEvents (one object or a JSON array) to ``TELEMETRY_ENDPOINT``. This module only *listens*:
it never talks back to Honcho and never writes to its database. Nothing here needs Qt, so it is tested on its own.

* ``TelemetryReceiver`` accepts the posts (optionally guarded by a shared secret header).
* ``EventLog`` keeps every event in full on disk, one ``events-YYYYMMDD.jsonl`` per day, size-capped.
* ``TelemetryStore`` keeps recent events in memory so the UI can match a question to its ``dialectic.completed``
  event (token counts, server time, iterations) and show the LLM calls behind it.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

KEY_HEADER = "X-Telemetry-Key"
MAX_BODY = 100 * 1024 * 1024  # a batch of full prompt/response traces can be tens of MB
DEFAULT_LOG_CAP = 1024 ** 3  # 1 GB of logs, oldest day deleted first; 0 = unlimited
DIALECTIC = "dialectic.completed"


def extract_events(payload) -> list[dict]:
    """Normalise whatever was posted into CloudEvent dicts (``type``, ``time``, ``data``, ...).

    Accepts one event, a JSON array of events, and the ``{"received_at": ..., "event": {...}}`` wrapper that
    this module's own log files use.
    """
    items = payload if isinstance(payload, list) else [payload]
    events = []
    for item in items:
        if isinstance(item, dict) and "type" not in item and isinstance(item.get("event"), dict):
            item = item["event"]
        if isinstance(item, dict) and item.get("type"):
            events.append(item)
    return events


def _is_bulky(event: dict) -> bool:
    kind = str(event.get("type", ""))
    return kind.endswith(".traced") or kind == "trace.content"


def _light(event: dict) -> dict:
    """Memory copy of an event: full-content trace payloads stay on disk only (they can be megabytes each)."""
    if not _is_bulky(event):
        return event
    data = event.get("data") or {}
    return {"type": event.get("type"), "time": event.get("time"),
            "data": {"run_id": data.get("run_id"), "_omitted": "full payload is in the log file"}}


class TelemetryStore:
    """Recent events, newest last. Thread-safe: the receiver adds from its own thread, the UI reads."""

    def __init__(self, max_events: int = 20000):
        self._lock = threading.Lock()
        self._events: deque[tuple[float, dict]] = deque(maxlen=max_events)
        self.total = 0

    def add(self, events: list[dict], received_at: float | None = None) -> int:
        stamp = received_at if received_at is not None else time.time()
        with self._lock:
            for event in events:
                self._events.append((stamp, _light(event)))
            self.total += len(events)
        return len(events)

    def snapshot(self) -> list[tuple[float, dict]]:
        with self._lock:
            return list(self._events)

    def find_dialectic(self, workspace: str, peer: str, level: str, since: float,
                       claimed: frozenset | set = frozenset()) -> dict | None:
        """The earliest unclaimed ``dialectic.completed`` for this question received at/after ``since``."""
        for received, event in self.snapshot():
            data = event.get("data") or {}
            if (received >= since and event.get("type") == DIALECTIC and data.get("workspace_name") == workspace
                    and data.get("peer_name") == peer and data.get("reasoning_level") == level
                    and data.get("run_id") not in claimed):
                return event
        return None

    def run_events(self, run_id: str) -> list[dict]:
        return [e for _, e in self.snapshot() if (e.get("data") or {}).get("run_id") == run_id]


def summarize_run(dialectic: dict, children: list[dict]) -> dict:
    """The numbers worth comparing for one question, from its dialectic event and the LLM calls behind it."""
    data = dialectic.get("data") or {}
    calls = [c.get("data") or {} for c in children if c.get("type") == "llm.call.completed"]
    models = []
    for call in calls:
        if call.get("model") and call["model"] not in models:
            models.append(call["model"])
    ms = data.get("total_duration_ms")
    return {"run_id": data.get("run_id"), "input_tokens": data.get("input_tokens"),
            "output_tokens": data.get("output_tokens"), "cache_read_tokens": data.get("cache_read_tokens"),
            "server_s": round(ms / 1000, 1) if isinstance(ms, (int, float)) else None,
            "iterations": data.get("total_iterations"), "tool_calls": data.get("tool_calls_count"),
            "prefetched": data.get("prefetched_conclusion_count"), "llm_calls": len(calls), "models": models}


class EventLog:
    """Every received event, in full, as JSON lines: ``events-YYYYMMDD.jsonl`` (UTC day), oldest pruned past the cap."""

    def __init__(self, folder: Path, max_bytes: int = DEFAULT_LOG_CAP):
        self.folder = Path(folder)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._last_prune = 0.0

    def write(self, events: list[dict], received_at: float | None = None) -> None:
        stamp = received_at if received_at is not None else time.time()
        moment = datetime.fromtimestamp(stamp, timezone.utc)
        lines = "".join(json.dumps({"received_at": moment.isoformat(), "event": e}, ensure_ascii=False) + "\n"
                        for e in events)
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.folder / f"events-{moment:%Y%m%d}.jsonl", "a", encoding="utf-8") as f:
                f.write(lines)
            if self.max_bytes and time.monotonic() - self._last_prune > 30:
                self._last_prune = time.monotonic()
                self._prune()

    def _prune(self) -> None:
        files = sorted(self.folder.glob("events-*.jsonl"))
        sizes = {f: f.stat().st_size for f in files}
        total = sum(sizes.values())
        for f in files[:-1]:  # never delete the file being written
            if total <= self.max_bytes:
                break
            total -= sizes[f]
            f.unlink(missing_ok=True)


def parse_allowed(text: str) -> frozenset[str]:
    """'192.168.1.22, 10.0.0.5' -> {'192.168.1.22', '10.0.0.5'} (commas, spaces or semicolons)."""
    return frozenset(p for p in text.replace(",", " ").replace(";", " ").split() if p)


def lan_address() -> str:
    """This PC's address on the local network (what a Honcho server would use to reach us)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # UDP connect sends nothing; it only picks the outgoing interface
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


class _ExclusiveServer(ThreadingHTTPServer):
    """Refuses a port that is already taken. The default (address reuse) lets a second copy of the app bind the
    same port on Windows, and the first one silently stops receiving."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class TelemetryReceiver:
    def __init__(self, store: TelemetryStore, log: EventLog | None = None, key: str = "",
                 on_events: Callable[[int], None] | None = None, allowed: frozenset[str] = frozenset(),
                 on_batch: Callable[[list[dict], float], None] | None = None):
        self.store, self.log, self.key, self.on_events = store, log, key, on_events
        self.on_batch = on_batch  # called with (events, received_at) from the receiver's thread
        self.allowed = allowed  # sender IPs we accept posts from; empty = anyone who can reach the port
        self.refused = 0
        self.last_error = ""
        self.bad_posts = 0
        self._server: ThreadingHTTPServer | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int | None:
        return self._server.server_address[1] if self._server else None

    def start(self, port: int, host: str = "0.0.0.0") -> int:
        if self._server:
            return self.port or port
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _reply(self, status: int, text: str) -> None:
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._reply(200, "Honcho Viewer telemetry listener: POST CloudEvents here.\n")

            def do_POST(self) -> None:
                if receiver.allowed and self.client_address[0] not in receiver.allowed:
                    receiver.refused += 1
                    self._reply(403, "this address is not allowed to post here\n")
                    return
                if receiver.key and self.headers.get(KEY_HEADER) != receiver.key:
                    self._reply(401, f"missing or wrong {KEY_HEADER} header\n")
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._reply(413, "body too large\n")
                    return
                try:
                    events = extract_events(json.loads(self.rfile.read(length) or b"null"))
                except ValueError:
                    events = []
                if not events:
                    receiver.bad_posts += 1
                    self._reply(400, "no CloudEvents found in the body\n")
                    return
                receiver.accept(events)
                self._reply(202, f"accepted {len(events)}\n")

        server = _ExclusiveServer((host, port), Handler)
        self._server = server
        threading.Thread(target=server.serve_forever, daemon=True, name="telemetry-receiver").start()
        return server.server_address[1]

    def accept(self, events: list[dict]) -> None:
        stamp = time.time()
        if self.log:
            try:
                self.log.write(events, stamp)
            except OSError as exc:  # disk full / folder gone: keep receiving, tell the user
                self.last_error = f"Cannot write the log: {exc}"
        self.store.add(events, stamp)
        if self.on_batch:
            self.on_batch(events, stamp)
        if self.on_events:
            self.on_events(len(events))

    def stop(self) -> None:
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()
