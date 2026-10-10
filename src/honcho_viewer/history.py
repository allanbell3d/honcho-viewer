"""History: what happened in a workspace over time, rebuilt from Honcho's telemetry events.

Reads the viewer's own log, any other log files you open, and live events, and groups them into *jobs*
(extraction, dream, question, summary) with their status, cost, what they wrote and their steps. Nothing here
needs Qt, and nothing talks to Honcho. The grouping rules are in docs/design/history-tab.md.
"""
from __future__ import annotations

import gzip
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .telemetry import extract_events

JOB_KINDS = ("extraction", "dream", "question", "summary")
EVENT_KINDS = ("messages", "deletion", "context", "maintenance", "call", "other")
KIND_TITLES = {"extraction": "extraction", "dream": "dream", "question": "question", "summary": "summary",
               "messages": "messages", "deletion": "deletion", "context": "context read",
               "maintenance": "maintenance", "call": "model call", "other": "event"}
NO_WORKSPACE = "(no workspace)"
SKIP_TYPES = ("trace.content",)  # prompt/answer text: most of the bytes, not needed for the timeline
_DROP_FIELDS = {"client", "honcho_version", "output_signatures", "input_message_refs", "output_tool_calls",
                "system_prompt_refs", "queue_item_ids", "source_message_ids", "observers"}


def parse_time(text) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _n(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _compact(data: dict) -> dict:
    """The fields worth showing for one step: no references to stored prompts, no long text."""
    out = {}
    for key, value in data.items():
        if key in _DROP_FIELDS or key.endswith("_ref") or key.endswith("_refs") or value in (None, "", [], {}):
            continue
        if isinstance(value, str) and len(value) > 300:
            value = value[:300] + "…"
        out[key] = value
    return out


# ---- reading log files ------------------------------------------------------------------------------------
def log_files(paths) -> list[Path]:
    """Every log file under the given files and folders (``*.jsonl``, ``*.jsonl.gz``, ``*.json``), oldest name first."""
    found: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            found += [f for f in p.rglob("*") if f.is_file() and f.name.endswith((".jsonl", ".jsonl.gz", ".json"))]
        elif p.is_file():
            found.append(p)
    return sorted(set(found), key=lambda f: (f.name, str(f)))


def iter_log_file(path: Path, offset: int = 0):
    """Yield ``(events, bad_line)`` per line from ``offset`` and finally the offset reached (as an int).

    Lines may hold a CloudEvent, the viewer's ``{"received_at", "event"}`` wrapper, or a JSON array of either.
    A gzipped file is read whole (its offset is its size); an unfinished last line is left for the next read.
    """
    path = Path(path)
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                yield _parse_line(line)
        yield path.stat().st_size
        return
    with open(path, "rb") as f:
        f.seek(offset)
        position = offset
        for raw in f:
            if not raw.endswith(b"\n"):
                break  # still being written: read it next time
            position += len(raw)
            yield _parse_line(raw.decode("utf-8", errors="replace"))
    yield position


def _parse_line(line: str) -> tuple[list[dict], bool]:
    line = line.strip()
    if not line:
        return [], False
    if any(f'"{t}"' in line[:400] for t in SKIP_TYPES):
        return [], False
    try:
        return extract_events(json.loads(line)), False
    except ValueError:
        return [], True


# ---- jobs ------------------------------------------------------------------------------------------------
@dataclass
class Job:
    key: str
    kind: str
    workspace: str = NO_WORKSPACE
    start: float = 0.0
    end: float = 0.0
    peer: str = ""
    observer: str = ""
    session: str = ""
    models: list[str] = field(default_factory=list)
    call_in: int = 0
    call_out: int = 0
    call_cached: int = 0
    total_in: int | None = None  # from the job's own completed event, when there is one
    total_out: int | None = None
    created: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    card_updated: bool = False
    messages: int = 0
    calls: int = 0
    embeddings: int = 0
    errors: int = 0
    retries: int = 0
    fallbacks: int = 0
    final_error: str = ""
    done: bool = False
    partly: bool = False
    failed_event: bool = False
    info: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)

    @property
    def tokens_in(self) -> int:
        return self.total_in if self.total_in is not None else self.call_in

    @property
    def tokens_out(self) -> int:
        return self.total_out if self.total_out is not None else self.call_out

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def status(self) -> str:
        if self.kind in EVENT_KINDS:
            return "failed" if self.failed_event or self.final_error else "ok"
        if not self.done:
            return "failed" if self.final_error else "incomplete"
        if self.partly:
            return "partly failed"
        return "retried" if self.errors else "ok"

    @property
    def problem(self) -> bool:
        return self.status in ("failed", "partly failed")


class HistoryState:
    """Every job seen so far, from files and live events. Thread-safe: files load in the background."""

    def __init__(self):
        self._lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self._seen: set[str] = set()
        self._by_message: dict[tuple[str, str], str] = {}  # (workspace, message id) -> extraction job key
        self._by_summary: dict[tuple[str, int], str] = {}  # (workspace, input tokens) -> summary job key
        self._offsets: dict[str, int] = {}
        self.sources: list[str] = []
        self.bad_lines = 0
        self.events = 0
        self._have_traced = False
        self._pending_completed: list[tuple[dict, float]] = []

    # ---- input
    def add_events(self, events: list[dict], _received_at: float | None = None) -> int:
        added = 0
        with self._lock:
            for event in events:
                event_id = event.get("id")
                if event_id:
                    if event_id in self._seen:
                        continue
                    self._seen.add(event_id)
                if event.get("type") in SKIP_TYPES:
                    continue
                self._add(event)
                added += 1
            self.events += added
        return added

    def load_paths(self, paths) -> dict:
        """Read files and folders (remembered as sources). Returns counts for the status line."""
        with self._lock:
            for p in map(str, paths):
                if p not in self.sources:
                    self.sources.append(p)
        return self.refresh_files()

    def refresh_files(self) -> dict:
        """Read what is new in every source: new files, and new lines at the end of growing ones."""
        files = log_files(self.sources)
        before_events, before_bad = self.events, self.bad_lines
        for f in files:
            key = str(f)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            offset = self._offsets.get(key, 0)
            if size == offset or (f.name.endswith(".gz") and key in self._offsets):
                continue
            if size < offset:
                offset = 0  # the file was replaced: read it again (duplicates are dropped by id)
            batch, bad = [], 0
            try:
                for item in iter_log_file(f, offset):
                    if isinstance(item, int):
                        self._offsets[key] = item
                    else:
                        events, broken = item
                        batch += events
                        bad += broken
                        if len(batch) >= 5000:
                            self.add_events(batch)
                            batch = []
            except OSError:
                continue
            self.add_events(batch)
            with self._lock:
                self.bad_lines += bad
        return {"files": len(files), "events": self.events - before_events, "bad": self.bad_lines - before_bad}

    # ---- grouping
    def _job(self, key: str, kind: str) -> Job:
        job = self.jobs.get(key)
        if job is None:
            job = self.jobs[key] = Job(key, kind)
        elif job.kind in ("call", "other") and kind not in ("call", "other"):
            job.kind = kind
        return job

    def _add(self, event: dict) -> None:
        kind, data = event.get("type", ""), event.get("data") or {}
        if not isinstance(data, dict):
            return
        stamp = parse_time(event.get("time")) or parse_time(data.get("timestamp"))
        if stamp is None:
            return
        run_id = data.get("run_id")
        if run_id:
            category = data.get("parent_category")
            job_kind = ("question" if kind == "dialectic.completed" or category == "dialectic" else
                        "dream" if kind.startswith("dream.") or category == "dream" else
                        "extraction" if category == "representation" else
                        "summary" if category == "summary" else "call")
            job = self._job(f"run:{run_id}", job_kind)
            if kind.endswith(".traced"):  # same numbers as its *.completed twin; only who and where are new
                job.workspace = data.get("workspace_name") or job.workspace
                job.peer = job.peer or data.get("peer_name") or data.get("observed") or ""
                job.session = job.session or data.get("session_name") or ""
                return
            self._apply(job, kind, data, stamp)
            return
        if kind == "llm.call.traced":
            self._have_traced = True
            self._pending_completed = []  # the traced copies carry these calls, with more detail
            self._traced_call(data, stamp)
        elif kind == "llm.call.completed":
            if not self._have_traced:  # Honcho may not send traced events at all: kept until we know
                self._pending_completed.append((data, stamp))
        elif kind in ("embedding.call.completed",) and data.get("outcome") == "error":
            self._apply(self._job(f"evt:{event.get('id') or stamp}", "call"), kind, data, stamp)
        elif kind == "representation.completed":
            self._extraction_done(event, data, stamp)
        elif kind == "agent.tool.summary.created":
            self._summary_created(event, data, stamp)
        elif kind.endswith(".traced") or kind.startswith("embedding."):
            return
        else:
            job_kind = ("messages" if kind == "message.created" else "deletion" if kind.startswith("deletion.") else
                        "context" if kind == "context.retrieved" else
                        "maintenance" if kind.startswith("reconciliation.") else "other")
            self._apply(self._job(f"evt:{event.get('id') or (kind, stamp)}", job_kind), kind, data, stamp)

    def _flush_completed(self) -> None:
        """No traced events at all: count the calls without a run on their own (no retry grouping)."""
        if self._have_traced:
            return
        for data, stamp in self._pending_completed:
            purpose = str(data.get("call_purpose") or "")
            job_kind = ("extraction" if purpose.startswith("deriver") else
                        "summary" if purpose.startswith("summary") else "call")
            self._apply(self._job(f"call:{stamp}:{purpose}", job_kind), "llm.call.completed", data, stamp)
        self._pending_completed = []

    def _traced_call(self, data: dict, stamp: float) -> None:
        purpose = str(data.get("call_purpose") or "")
        job_kind = ("extraction" if purpose.startswith("deriver") else
                    "summary" if purpose.startswith("summary") else "call")
        ws = data.get("workspace_name") or NO_WORKSPACE
        key = f"trace:{data.get('trace_id') or stamp}"
        for message_id in (data.get("source_message_ids") or []) if job_kind == "extraction" else []:
            linked = self._by_message.get((ws, message_id))
            if linked and linked != key and linked in self.jobs:
                key = linked  # the batch event arrived first: join it
                break
        tokens = _n(data.get("provider_input_tokens"))
        if job_kind == "summary" and data.get("outcome") != "error":
            # its summary.created event reports the same input tokens; whichever arrives first waits for the other
            key = self._by_summary.pop((ws, tokens), None) or key
            if key.startswith("trace:"):
                self._by_summary[(ws, tokens)] = key
        job = self._job(key, job_kind)
        self._apply(job, "llm.call.completed", data, stamp)
        if job_kind == "extraction":
            for message_id in data.get("source_message_ids") or []:
                self._by_message[(ws, message_id)] = key

    def _extraction_done(self, event: dict, data: dict, stamp: float) -> None:
        ws = data.get("workspace_name") or NO_WORKSPACE
        key = None
        for message_id in (data.get("latest_message_id"), data.get("earliest_message_id")):
            linked = self._by_message.get((ws, message_id))
            if linked in self.jobs and not self.jobs[linked].done:
                key = linked
                break
        if key is None:
            key = f"rep:{event.get('id') or stamp}"
            for message_id in (data.get("latest_message_id"), data.get("earliest_message_id")):
                if message_id:
                    self._by_message[(ws, message_id)] = key
        self._apply(self._job(key, "extraction"), "representation.completed", data, stamp)

    def _summary_created(self, event: dict, data: dict, stamp: float) -> None:
        ws = data.get("workspace_name") or NO_WORKSPACE
        link = (ws, _n(data.get("input_tokens")))
        key = self._by_summary.pop(link, None)
        if key is None:
            key = self._by_summary[link] = f"evt:{event.get('id') or stamp}"
        self._apply(self._job(key, "summary"), "agent.tool.summary.created", data, stamp)

    # ---- one event into one job
    def _apply(self, job: Job, kind: str, data: dict, stamp: float) -> None:
        duration_ms = data.get("total_duration_ms") or data.get("duration_ms")
        begin = stamp - duration_ms / 1000 if isinstance(duration_ms, (int, float)) else stamp
        job.start = begin if not job.start else min(job.start, begin)
        job.end = max(job.end, stamp)
        if data.get("workspace_name"):
            job.workspace = data["workspace_name"]
        job.peer = job.peer or data.get("peer_name") or data.get("observed") or ""
        job.observer = job.observer or data.get("observer") or ""
        job.session = job.session or data.get("session_name") or ""
        step = {"t": stamp, "type": kind, "ok": True, "text": kind, "fields": _compact(data)}

        if kind == "llm.call.completed":
            job.calls += 1
            model = data.get("model")
            if model and model not in job.models:
                job.models.append(model)
            job.call_in += _n(data.get("provider_input_tokens"))
            job.call_out += _n(data.get("provider_output_tokens"))
            job.call_cached += _n(data.get("cache_read_tokens"))
            attempt = _n(data.get("attempt")) or 1
            if attempt > 1:
                job.retries += 1
            if data.get("was_fallback"):
                job.fallbacks += 1
            what = f"model call · {data.get('call_purpose') or '?'} · {model or '?'}"
            if data.get("outcome") == "error":
                job.errors += 1
                final = data.get("is_final_attempt")
                if final:
                    job.final_error = str(data.get("error_class") or "error")
                step["ok"] = False
                step["text"] = (f"✗ {what} · {data.get('error_class') or 'error'} · attempt {attempt}"
                                f"{' (final)' if final else ' (will retry)'}")
            else:
                step["text"] = (f"{what} · {_n(data.get('provider_input_tokens'))} in / "
                                f"{_n(data.get('provider_output_tokens'))} out")
        elif kind == "embedding.call.completed":
            job.embeddings += 1
            if data.get("outcome") == "error":
                job.errors += 1
                if data.get("is_final_attempt"):
                    job.final_error = str(data.get("error_class") or "error")
                step["ok"] = False
                step["text"] = f"✗ embedding · {data.get('error_class') or 'error'}"
            else:
                return  # many per job; counted, not listed
        elif kind == "agent.iteration":
            tools = data.get("tool_calls") or []
            step["text"] = (f"iteration {data.get('iteration')} · {_n(data.get('input_tokens'))} in / "
                            f"{_n(data.get('output_tokens'))} out" + (f" · tools: {', '.join(map(str, tools))}"
                                                                      if tools else ""))
        elif kind == "agent.tool.call.completed":
            step["ok"] = not data.get("is_error")
            step["text"] = (f"{'✗ ' if data.get('is_error') else ''}tool {data.get('tool_name')} · "
                            f"{_n(data.get('results_count'))} results")
        elif kind == "agent.tool.conclusions.created":
            step["text"] = f"wrote {_n(data.get('conclusion_count'))} conclusions ({', '.join(data.get('levels') or [])})"
        elif kind == "agent.tool.conclusions.deleted":
            step["text"] = f"deleted {_n(data.get('conclusion_count'))} conclusions ({', '.join(data.get('levels') or [])})"
        elif kind == "agent.tool.peer_card.updated":
            job.card_updated = True
            step["text"] = f"peer card updated ({_n(data.get('facts_count'))} facts)"
        elif kind == "dialectic.completed":
            job.done = True
            job.total_in, job.total_out = _n(data.get("input_tokens")), _n(data.get("output_tokens"))
            job.info.update(_compact(data))
            step["text"] = (f"answered · level {data.get('reasoning_level')} · {data.get('total_iterations')} "
                            f"iterations · {data.get('tool_calls_count')} tool calls")
        elif kind == "dream.specialist":
            for level, count in (data.get("created_counts_by_level") or {}).items():
                job.created[level] = job.created.get(level, 0) + _n(count)
            for level, count in (data.get("deleted_counts_by_level") or {}).items():
                job.deleted[level] = job.deleted.get(level, 0) + _n(count)
            job.card_updated = job.card_updated or bool(data.get("peer_card_updated"))
            ok = data.get("success") is not False
            job.partly = job.partly or not ok
            step["ok"] = ok
            step["text"] = (f"{'✗ ' if not ok else ''}specialist {data.get('specialist_type')} · "
                            f"+{_n(data.get('created_observation_count'))} −{_n(data.get('deleted_observation_count'))}"
                            + (f" · {data.get('error_class')}" if not ok and data.get('error_class') else ""))
        elif kind == "dream.run":
            job.done = True
            job.total_in, job.total_out = _n(data.get("total_input_tokens")), _n(data.get("total_output_tokens"))
            job.info.update(_compact(data))
            step["text"] = (f"dream {data.get('dream_type')} finished · trigger {data.get('trigger_reason')} · "
                            f"specialists {', '.join(data.get('specialists_run') or [])}")
        elif kind == "representation.completed":
            job.done = True
            job.total_in, job.total_out = _n(data.get("total_input_tokens")), _n(data.get("output_tokens"))
            explicit = _n(data.get("explicit_conclusion_count"))
            if explicit:
                job.created["explicit"] = job.created.get("explicit", 0) + explicit
            job.messages += _n(data.get("message_count"))
            job.partly = job.partly or _n(data.get("failed_observer_count")) > 0
            job.info.update(_compact(data))
            step["text"] = (f"batch done · {_n(data.get('message_count'))} messages → {explicit} conclusions"
                            + (f" · {_n(data.get('failed_observer_count'))} observer(s) failed"
                               if data.get("failed_observer_count") else ""))
        elif kind == "agent.tool.summary.created":
            job.done = True
            job.total_in, job.total_out = _n(data.get("input_tokens")), _n(data.get("output_tokens"))
            job.info.update(_compact(data))
            step["text"] = f"{data.get('summary_type')} summary written ({_n(data.get('message_count'))} messages)"
        elif kind == "message.created":
            job.messages += _n(data.get("message_count"))
            step["text"] = f"+{_n(data.get('message_count'))} messages ({data.get('source') or '?'})"
        elif kind.startswith("deletion."):
            job.failed_event = data.get("success") is False
            job.deleted["conclusions"] = _n(data.get("conclusions_deleted"))
            job.messages = -_n(data.get("messages_deleted"))
            job.info.update(_compact(data))
            step["ok"] = not job.failed_event
            step["text"] = (f"{data.get('deletion_type')} {data.get('resource_id')} deleted · "
                            f"{_n(data.get('messages_deleted'))} messages, {_n(data.get('conclusions_deleted'))} "
                            f"conclusions" + (f" · {data.get('error_message')}" if data.get("error_message") else ""))
        else:
            job.info.update(_compact(data))
        job.steps.append(step)

    # ---- queries
    def workspaces(self) -> list[str]:
        with self._lock:
            self._flush_completed()
            return sorted({j.workspace for j in self.jobs.values()}, key=str.lower)

    def peers(self, workspace: str) -> list[str]:
        with self._lock:
            return sorted({j.peer for j in self.jobs.values() if j.workspace == workspace and j.peer}, key=str.lower)

    def sessions(self, workspace: str) -> list[str]:
        with self._lock:
            return sorted({j.session for j in self.jobs.values() if j.workspace == workspace and j.session},
                          key=str.lower)

    def select(self, workspace: str, kinds=None, start: float | None = None, end: float | None = None,
               peer: str | None = None, session: str | None = None, problems_only: bool = False) -> list[Job]:
        """Jobs of one workspace that match, newest first."""
        with self._lock:
            self._flush_completed()
            jobs = [j for j in self.jobs.values() if j.workspace == workspace
                    and (kinds is None or j.kind in kinds)
                    and (start is None or j.end >= start) and (end is None or j.start <= end)
                    and (not peer or j.peer == peer or j.observer == peer)
                    and (not session or j.session == session)
                    and (not problems_only or j.problem)]
        return sorted(jobs, key=lambda j: j.end, reverse=True)

    @staticmethod
    def totals(jobs: list[Job]) -> dict:
        t = {"jobs": len(jobs), "failed": 0, "partly": 0, "incomplete": 0, "tokens_in": 0, "tokens_out": 0,
             "created": 0, "deleted": 0, "by_kind": {}}
        for j in jobs:
            t["by_kind"][j.kind] = t["by_kind"].get(j.kind, 0) + 1
            status = j.status
            t["failed"] += status == "failed"
            t["partly"] += status == "partly failed"
            t["incomplete"] += status == "incomplete"
            t["tokens_in"] += j.tokens_in
            t["tokens_out"] += j.tokens_out
            if j.kind != "deletion":
                t["created"] += sum(j.created.values())
                t["deleted"] += sum(j.deleted.values())
        return t
