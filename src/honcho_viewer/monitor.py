"""Live aggregation of Honcho telemetry per workspace, for the Monitor tab. No Qt: tested on its own.

Feed it CloudEvents (``ingest``) and, optionally, queue status polled from the API (``set_queue``); read back one
summary per workspace plus fleet totals. Everything here is a rolling aggregate: raw events are not kept (the
telemetry log on disk has them), only short human-readable lines for the detail view.

Which events mean what (from Honcho's telemetry schema, every one carries ``workspace_name``):

* ``representation.completed``   extraction: a batch of messages turned into conclusions
* ``dream.run`` / ``dream.specialist``   dreaming: a run, and each specialist's created/deleted conclusions
* ``dialectic.completed``        a question answered
* ``llm.call.completed``         every model call: purpose, model, tokens, outcome, error class, retries, fallback
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime

NO_WORKSPACE = "(no workspace)"
ACTIVE_WITHIN_S = 15
DEFAULT_STALL_S = 180
RATE_WINDOW_S = 60
QUIET_TYPES = ("llm.call.traced", "embedding.call.traced", "trace.content")  # bulky; counted, never listed


def _clock(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime("%H:%M:%S")


def _n(value) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


class _Ws:
    def __init__(self, recent: int):
        self.events = 0
        self.last_event: float | None = None
        self.last_kind = ""
        self.messages_created = 0
        self.extract_batches = self.extract_messages = self.extract_conclusions = self.extract_failed_observers = 0
        self.dream_runs = self.specialists = self.specialists_failed = 0
        self.dream_created = self.dream_deleted = 0
        self.questions = 0
        self.calls = self.tokens_in = self.tokens_out = 0
        self.errors = self.retries = self.fallbacks = 0
        self.models: dict[str, int] = {}
        self.purposes: dict[str, dict] = {}
        self.call_times: deque[tuple[float, int]] = deque()  # (received_at, tokens) for the rate window
        self.recent: deque[tuple[float, str]] = deque(maxlen=recent)
        self.error_log: deque[dict] = deque(maxlen=50)
        self.queue: dict | None = None
        self.queue_at: float | None = None


class MonitorState:
    def __init__(self, clock=time.time, stall_after: float = DEFAULT_STALL_S, recent: int = 200):
        self._lock = threading.Lock()
        self._clock = clock
        self._recent = recent
        self.stall_after = stall_after
        self._ws: dict[str, _Ws] = {}
        self._event_times: deque[float] = deque()

    # ---- feeding
    def _get(self, name: str) -> _Ws:
        if name not in self._ws:
            self._ws[name] = _Ws(self._recent)
        return self._ws[name]

    def ingest(self, events: list[dict], received_at: float | None = None) -> None:
        stamp = received_at if received_at is not None else self._clock()
        with self._lock:
            for event in events:
                self._ingest_one(event, stamp)
            cutoff = stamp - 60
            while self._event_times and self._event_times[0] < cutoff:
                self._event_times.popleft()

    def _ingest_one(self, event: dict, stamp: float) -> None:
        kind = str(event.get("type", ""))
        data = event.get("data") or {}
        ws = self._get(data.get("workspace_name") or NO_WORKSPACE)
        ws.events += 1
        ws.last_event = stamp
        self._event_times.append(stamp)
        line = None
        if kind == "representation.completed":
            ws.extract_batches += 1
            ws.extract_messages += _n(data.get("message_count"))
            ws.extract_conclusions += _n(data.get("explicit_conclusion_count"))
            ws.extract_failed_observers += _n(data.get("failed_observer_count"))
            ws.last_kind = "extracting"
            line = (f"extraction: {_n(data.get('message_count'))} messages -> "
                    f"{_n(data.get('explicit_conclusion_count'))} conclusions about {data.get('observed')}, "
                    f"{_n(data.get('total_input_tokens') or data.get('input_tokens'))} in / "
                    f"{_n(data.get('output_tokens'))} out tokens, {_n(data.get('total_duration_ms')) / 1000:.1f}s")
        elif kind == "dream.run":
            ws.dream_runs += 1
            ws.last_kind = "dreaming"
            line = (f"dream run ({data.get('dream_type') or 'dream'}) for {data.get('observed')}: "
                    f"{_n(data.get('total_iterations'))} iterations, {_n(data.get('total_input_tokens'))} in / "
                    f"{_n(data.get('total_output_tokens'))} out tokens, {_n(data.get('total_duration_ms')) / 1000:.1f}s")
        elif kind == "dream.specialist":
            ws.specialists += 1
            ws.dream_created += _n(data.get("created_observation_count"))
            ws.dream_deleted += _n(data.get("deleted_observation_count"))
            ws.last_kind = "dreaming"
            ok = data.get("success", True)
            if not ok:
                ws.specialists_failed += 1
                ws.error_log.append({"at": stamp, "what": f"dream specialist {data.get('specialist_type')}",
                                     "model": "", "error": data.get("error_class") or "failed"})
            line = (f"dream specialist {data.get('specialist_type')}: +{_n(data.get('created_observation_count'))} "
                    f"-{_n(data.get('deleted_observation_count'))} conclusions"
                    + ("" if ok else f" FAILED ({data.get('error_class') or 'error'})"))
        elif kind == "dialectic.completed":
            ws.questions += 1
            ws.last_kind = "questions"
            line = (f"question about {data.get('peer_name')} at {data.get('reasoning_level')}: "
                    f"{_n(data.get('input_tokens'))} in / {_n(data.get('output_tokens'))} out tokens, "
                    f"{_n(data.get('total_duration_ms')) / 1000:.1f}s")
        elif kind == "llm.call.completed":
            self._llm_call(ws, data, stamp)
            line = None
        elif kind == "message.created":
            ws.messages_created += 1
        if line is None and kind not in QUIET_TYPES and kind not in ("llm.call.completed", "message.created"):
            line = kind
        if line:
            ws.recent.append((stamp, line))

    def _llm_call(self, ws: _Ws, data: dict, stamp: float) -> None:
        ins, outs = _n(data.get("provider_input_tokens")), _n(data.get("provider_output_tokens"))
        ws.calls += 1
        ws.tokens_in += ins
        ws.tokens_out += outs
        purpose = str(data.get("call_purpose") or "other")
        bucket = ws.purposes.setdefault(purpose, {"calls": 0, "in": 0, "out": 0, "errors": 0})
        bucket["calls"] += 1
        bucket["in"] += ins
        bucket["out"] += outs
        if data.get("model"):
            ws.models[data["model"]] = ws.models.get(data["model"], 0) + 1
        if _n(data.get("attempt")) > 1:
            ws.retries += 1
        if data.get("was_fallback"):
            ws.fallbacks += 1
        if data.get("outcome", "success") != "success":
            ws.errors += 1
            bucket["errors"] += 1
            ws.error_log.append({"at": stamp, "what": purpose, "model": data.get("model") or "",
                                 "error": data.get("error_class") or data.get("outcome")})
            ws.recent.append((stamp, f"ERROR {purpose} on {data.get('model')}: "
                                     f"{data.get('error_class') or data.get('outcome')} (attempt {_n(data.get('attempt'))})"))
        ws.call_times.append((stamp, ins + outs))

    def set_queue(self, workspace: str, status: dict | None) -> None:
        """Queue status as returned by Honcho (``total_work_units`` etc.), or None if it could not be read."""
        with self._lock:
            ws = self._get(workspace)
            ws.queue = status
            ws.queue_at = self._clock()

    def watch(self, workspace: str) -> None:
        """Show a workspace even before anything has been received for it."""
        with self._lock:
            self._get(workspace)

    def clear(self) -> None:
        with self._lock:
            self._ws.clear()
            self._event_times.clear()

    # ---- reading
    def workspaces(self) -> list[str]:
        with self._lock:
            return list(self._ws)

    def _derive(self, ws: _Ws, now: float) -> dict:
        age = None if ws.last_event is None else now - ws.last_event
        queue = ws.queue or {}
        total, done = _n(queue.get("total_work_units")), _n(queue.get("completed_work_units"))
        running, waiting = _n(queue.get("in_progress_work_units")), _n(queue.get("pending_work_units"))
        working = bool(running or waiting)
        if working and (age is None or age > self.stall_after):
            state = "stalled"
        elif age is not None and age <= ACTIVE_WITHIN_S:
            state = "active"
        elif ws.queue is not None and total and not working:
            state = "done"
        else:
            state = "idle"
        recent_calls = [t for t in ws.call_times if now - t[0] <= RATE_WINDOW_S]
        return {
            "state": state, "age": age, "last_kind": ws.last_kind, "events": ws.events,
            "queue": ws.queue, "queue_total": total, "queue_done": done, "queue_running": running,
            "queue_waiting": waiting, "queue_age": None if ws.queue_at is None else now - ws.queue_at,
            "extract_batches": ws.extract_batches, "extract_messages": ws.extract_messages,
            "extract_conclusions": ws.extract_conclusions, "extract_failed_observers": ws.extract_failed_observers,
            "dream_runs": ws.dream_runs, "specialists": ws.specialists, "specialists_failed": ws.specialists_failed,
            "dream_created": ws.dream_created, "dream_deleted": ws.dream_deleted, "questions": ws.questions,
            "messages_created": ws.messages_created, "calls": ws.calls, "tokens_in": ws.tokens_in,
            "tokens_out": ws.tokens_out, "errors": ws.errors, "retries": ws.retries, "fallbacks": ws.fallbacks,
            "calls_per_min": len(recent_calls), "tokens_per_min": sum(t for _, t in recent_calls),
            "models": sorted(ws.models, key=ws.models.get, reverse=True),
        }

    def snapshot(self) -> dict[str, dict]:
        now = self._clock()
        with self._lock:
            return {name: self._derive(ws, now) for name, ws in self._ws.items()}

    def totals(self) -> dict:
        now = self._clock()
        with self._lock:
            rows = [self._derive(ws, now) for ws in self._ws.values()]
            recent = sum(1 for t in self._event_times if now - t <= 10)
        states = [r["state"] for r in rows]
        return {"workspaces": len(rows), "active": states.count("active"), "stalled": states.count("stalled"),
                "idle": states.count("idle"), "done": states.count("done"), "events_per_s": round(recent / 10, 1),
                "calls": sum(r["calls"] for r in rows), "tokens_in": sum(r["tokens_in"] for r in rows),
                "tokens_out": sum(r["tokens_out"] for r in rows), "errors": sum(r["errors"] for r in rows),
                "calls_per_min": sum(r["calls_per_min"] for r in rows)}

    def recent(self, workspace: str, limit: int = 200) -> list[tuple[float, str]]:
        """Newest first."""
        with self._lock:
            ws = self._ws.get(workspace)
            return list(reversed(ws.recent))[:limit] if ws else []

    def purposes(self, workspace: str) -> dict[str, dict]:
        with self._lock:
            ws = self._ws.get(workspace)
            return {k: dict(v) for k, v in ws.purposes.items()} if ws else {}

    def errors(self, workspace: str) -> list[dict]:
        with self._lock:
            ws = self._ws.get(workspace)
            return list(reversed(ws.error_log)) if ws else []

    @staticmethod
    def clock_text(stamp: float) -> str:
        return _clock(stamp)
