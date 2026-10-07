# Changelog

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
