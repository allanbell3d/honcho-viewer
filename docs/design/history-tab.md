# Design: the History tab

Status: implemented in 0.1.4 (2026-10-10).

## Goal

See everything that happened in a workspace over time: which jobs ran (extraction, dreams, questions, summaries),
which finished and which failed and why, what each one wrote, what is waiting, and how much it cost. The Monitor
shows the *current* state of many workspaces; History shows the *past* of one, down to single model calls.

The viewer stays a live, read-only tool. History is a **reader**: it shows the events the viewer received while
listening, and any log files you open. It does not record anything new and has no dependency on other hardware.

## Sources

* The viewer's own log (`local/telemetry/events-*.jsonl`), read automatically.
* **Open logs…**: any other files or folders of Honcho telemetry. Accepted formats: one JSON value per line, each
  either a CloudEvent, the `{"received_at": ..., "event": {...}}` wrapper the viewer writes, or a JSON array of
  either (the CloudEvents batch form); and `.jsonl.gz`. The chosen folder is remembered and re-read on start.
* Live events from the existing listener, while it runs.

Events are de-duplicated by CloudEvent `id`, so the same event from the live feed and from a file counts once.
`trace.content` events (prompt and answer text) are skipped: they are most of the bytes and the timeline does not
need them. Unreadable lines are counted and reported, never fatal. The viewer's own log format is unchanged.

Measured: a 97 MB day of real events reads in about 0.5 s with `trace.content` skipped, so there is no index
database. Files are read in the background and only new bytes of a growing file are read again. If loading ever
becomes slow, a persistent index can be added behind the same interface.

## Jobs: how events are grouped (verified against real logs, Honcho 3.2.2)

| Job | Grouped by | Done when | Failed when |
|---|---|---|---|
| question | `run_id` | `dialectic.completed` | no completed event and a final-attempt LLM error |
| dream | `run_id` | `dream.run` | a specialist with `success: false` (partly failed), or no `dream.run` and a final error |
| extraction | the call's `trace_id`, linked to `representation.completed` by message id | `representation.completed` | no completed event and a final-attempt LLM error |
| summary | the call's `trace_id`, linked to `agent.tool.summary.created` by equal input tokens | the call succeeded | final-attempt LLM error |

* Honcho sends `run_id: null` for extraction and summary events, so they cannot be grouped by run. Each extraction
  call (`llm.call.traced`, purpose `deriver.representation`) lists the messages it read (`source_message_ids`) and
  its retries share a `trace_id`; `representation.completed` names its `earliest_message_id` and
  `latest_message_id`. Same workspace and same message id is an exact link (658 of 658 batches in a real day).
* Events without a job (`message.created`, `deletion.completed`, `reconciliation.*`, `context.retrieved`) are
  timeline rows of their own.
* A job that is neither done nor failed is shown as *incomplete* (still running, or its events are not in the log).
* Status: *ok*, *retried* (ok after errors), *partly failed* (failed observers or specialists), *failed*,
  *incomplete*.
* Each job keeps: workspace, kind, start, end, peer(s), session, models, tokens in / out / cached, what it wrote
  (conclusions created and deleted by level, peer card updated, messages, deletions), errors and retries, and its
  steps (iterations, model calls, tool calls, writes) without any prompt text.

## The tab

* Top: workspace, period (or *Custom…* from / to), **Open logs…**, **Live**. Filters: job kinds, peer, session,
  *Problems only*. With no workspace chosen yet, the one with the most events is selected; a choice is remembered.
  *Other events* (context reads, maintenance) are off by default.
* **Pending now**: the workspace's queue counts, polled read-only every 10 s while the tab is visible. Honcho does
  not expose individual waiting jobs; the tooltip says so.
* **Totals** over what is shown: jobs by kind, failed, tokens, conclusions created and deleted.
* **Job table**: time, job, peer / session, status, duration, tokens, wrote, model(s), problems. Sortable; failed
  rows tinted; quiet gaps longer than 30 minutes appear as separator rows when sorted by time.
* **Detail** (click a job): its numbers and its steps in order; errors with class, attempt and fallback.
  **Show what it wrote** lists the conclusions the job created, fetched read-only with the conclusions list filtered
  by observed peer (and observer for dreams) and `created_at` inside the job's time window. Conclusions deleted
  since cannot be shown; if two jobs for the same peer overlap in time, the list is labelled as possibly mixed.

Out of scope: recording while the app is closed, editing or deleting logs, saving History views as pages.

## Code

* `history.py` (no Qt): log file reading (`iter_log_file`, `log_files`), `HistoryState` (thread-safe; `add_events`,
  `load_paths`, `refresh_files`, `select(...)`, `totals(...)`, `workspaces()`, `peers()`, `sessions()`).
* `history_view.py`: the tab. Live events reach it through the telemetry hub, which now feeds both the Monitor
  and History from the receiver's batch callback.
* Tests: unit tests for the readers (all formats, gz, bad lines, offsets, dedup) and the grouping rules (each job
  kind, retries, failures, the extraction link in both arrival orders); UI tests against the fake server.
