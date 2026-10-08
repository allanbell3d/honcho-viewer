# Changelog

## 0.1.2 (2026-10-08)

New: **Compare reasoning** tab and a live telemetry listener.

- Ask one peer the same question at several reasoning levels, one after another, with streamed answers, wall time,
  time to first words and the evidence stats (conclusions/messages read, tool calls).
- Optional **telemetry listener** (off by default): receives Honcho's CloudEvents (`TELEMETRY_ENDPOINT`), matches each
  answer to its `dialectic.completed` event and shows input/output/cached tokens, server time, iterations, LLM calls
  and models. Every event is logged in full to `local/telemetry/` (size-capped, default 1 GB). See `docs/telemetry.md`.
- **Save results** asks where to save (suggesting the dated default and remembering your last folder), and the three
  Compare tabs get **Open saved results…**, which loads a saved page back with all its views. Compare reasoning
  pages include the telemetry (summary and every event behind each run), so tokens and times come back too.
- **Snapshots** are dated files that are never overwritten (before, each Snapshot replaced the last one), saved
  through a file dialog, with **Load snapshot…** to choose the "before" for "What changed?". Old one-file snapshots
  are still found.
- The listener refuses a port that is already in use (on Windows, two copies of the app could otherwise bind the
  same port and the first one silently stopped receiving) and can optionally accept posts only from listed sender
  addresses.
- Chat client can stream (`chat_stream`). Compare tabs share more code: view and loading hooks in `ComparePanel`,
  one `WorkspaceMixin` for the workspace/peer pickers.

## 0.1.1 (2026-10-07), first public release

Everything below 0.1.1 was private development (0.0.1 to 0.0.4) and is kept for the record.

- Explore, rate and compare Honcho memory: peer card, representation, conclusions (from messages and from
  dreaming), messages, and Ask with every option exposed. Read-only by design.
- Compare models and Compare peers tabs, built on one shared engine (`compare_panel.py`), with Save results.
- No server address is built in: the field starts empty with an example placeholder, and the address you enter
  is remembered with the token.
- MIT license, README with screenshots, user guide, CONTRIBUTING and `pyproject.toml`.

## 0.0.4 (2026-10-07)

New: **Compare peers** tab. Pick one workspace, tick several of its peers (all ticked to start, including
the user), ask them all the same question and read the answers side by side.

- Same layout, views (Counts, Peer card, Representation, Conclusions, Answers) and "Save results" as Compare models.
- Both Compare tabs now share one engine (`compare_panel.py`); each tab only provides its own selector,
  so a future compare page is a small subclass and all pages stay identical in behaviour.
- Saved reports from this tab list the workspace and one section per peer.

## 0.0.3 (2026-10-07)

New: "Save results" on the Compare models page.

- One click saves everything currently loaded there as one dated page in
  `local\comparisons\<date-time>_<peer>.html`: the question and reasoning level, the counts table,
  and per workspace the answer, peer card, representation and all conclusions (collapsible).
- Opens in any browser. The raw data is embedded in the page so a later version can load it back.
- A second save never overwrites the first. The status bar says where it went, and names any
  workspace that was still loading.

## 0.0.2 (2026-10-06)

Fix: every fact showed twice and all counts were doubled.

- Agents that "observe others" keep their own identical copy of each fact about a peer. The
  Conclusions tab, snapshot / "what changed?" and Compare models now default to the peer's own
  view (By = the peer itself). "(anyone)" is still in the By: list and shows the copies.
- "What changed?" ignores other-observer rows in snapshots saved by 0.1.0
- smoke_test.py no longer crashes on Windows when its output is piped

Fix: the Overview showed only 25 facts, and long conclusion lists were cut off.

- The representation now asks for 100 conclusions (the server maximum; without asking, the server
  sends its 25 newest). The Overview says how many of the peer's conclusions that is, and the
  description no longer claims it is "everything Honcho believes".
- Conclusions lists are no longer capped at 5,000 rows (Dell-Manager's own view has 7,127).

## 0.0.1 (2026-10-06)

First version.

- Connect with server URL + token (remembered locally, git-ignored)
- Explore: workspaces (with your own model labels and queue status) → peers → Overview,
  Conclusions, Messages, Ask
- Conclusions: messages-vs-dreaming split, premises of dream conclusions, supporting-message
  search, correct/wrong/unsure ratings with accuracy, snapshot + "what changed?" diff
- Ask: every dialectic option with defaults, hover explanations and a live request preview
- Compare models: counts, peer card, representation, conclusions and "ask all" side by side
- Read-only client with a route allowlist; offline tests against a fake Honcho server
