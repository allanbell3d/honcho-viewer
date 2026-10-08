"""Pure HTML builders for the read-only panes. All server text is escaped."""
from __future__ import annotations

import json
import re
from html import escape

from .explain import LEVELS

LEVEL_COLORS = {"explicit": "#2f80ed", "deductive": "#9b51e0", "inductive": "#e08a2e", "contradiction": "#eb5757"}
RATING_MARKS = {"correct": ("✓", "#27ae60"), "wrong": ("×", "#eb5757"), "unsure": ("?", "#e0a82e")}
MUTED = "color:#888"


def esc(text) -> str:
    return escape("" if text is None else str(text)).replace("\n", "<br>")


def short_time(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ")


def origin(level: str) -> str:
    return "messages" if level == "explicit" else "dreaming"


def level_badge(level: str) -> str:
    color = LEVEL_COLORS.get(level, "#888")
    return f'<span style="color:{color};font-weight:bold">{esc(LEVELS.get(level, (level,))[0])}</span>'


def rating_mark(rating: str | None) -> str:
    if rating not in RATING_MARKS:
        return ""
    mark, color = RATING_MARKS[rating]
    return f'<span style="color:{color};font-weight:bold">{mark}</span> '


def placeholder(text: str) -> str:
    return f'<p style="{MUTED}"><i>{esc(text)}</i></p>'


def card_html(card: list[str] | None) -> str:
    if not card:
        return placeholder("No peer card yet. Honcho builds it after processing some messages.")
    return "<ul>" + "".join(f"<li>{esc(fact)}</li>" for fact in card) + "</ul>"


def text_html(text: str | None, empty: str = "Nothing here yet.") -> str:
    return f"<p>{esc(text)}</p>" if text else placeholder(empty)


def json_html(obj) -> str:
    return f"<pre>{escape(json.dumps(obj, indent=2, ensure_ascii=False))}</pre>"


def conclusion_line(c: dict, rating: str | None = None) -> str:
    who = "" if c.get("observer_id") == c.get("observed_id") else \
        f' <span style="{MUTED}">({esc(c.get("observer_id"))} about {esc(c.get("observed_id"))})</span>'
    return f"<li>{rating_mark(rating)}{level_badge(c.get('level', ''))} {esc(c.get('content'))}{who}</li>"


def conclusions_html(conclusions: list[dict], ratings: dict[str, str]) -> str:
    if not conclusions:
        return placeholder("No conclusions.")
    parts = []
    for title, group in (("From messages", "messages"), ("From dreaming", "dreaming")):
        rows = [c for c in conclusions if origin(c.get("level", "")) == group]
        parts.append(f"<h4>{title} ({len(rows)})</h4>")
        parts.append("<ul>" + "".join(conclusion_line(c, ratings.get(c["id"])) for c in rows) + "</ul>"
                     if rows else placeholder("None."))
    return "".join(parts)


def message_line(m: dict) -> str:
    return (f'<p><b>{esc(m.get("peer_id"))}</b> <span style="{MUTED}">{short_time(m.get("created_at"))}'
            f' · {esc(m.get("session_id"))}</span><br>{esc(m.get("content"))}</p>')


def messages_html(messages: list[dict]) -> str:
    return "".join(message_line(m) for m in messages) or placeholder("No messages.")


def conclusion_detail_html(c: dict, premises: list[dict] | None = None,
                           supporting: list[dict] | None = None) -> str:
    lvl = c.get("level", "")
    out = [f"<p>{level_badge(lvl)} <span style='{MUTED}'>{esc(LEVELS.get(lvl, ('', ''))[1])}</span></p>",
           f"<p style='font-size:large'>{esc(c.get('content'))}</p>",
           f"<p style='{MUTED}'>{esc(c.get('observer_id'))} about {esc(c.get('observed_id'))} · session "
           f"{esc(c.get('session_id') or '-')} · derived {c.get('times_derived', 1)}× · "
           f"{short_time(c.get('created_at'))} · id {esc(c.get('id'))}</p>"]
    if c.get("source_ids"):
        out.append("<h4>Built from</h4>")
        out.append("<ul>" + "".join(conclusion_line(p) for p in premises) + "</ul>" if premises
                   else placeholder("Loading premises…" if premises is None else "Premises not found."))
    if supporting is not None:
        out.append("<h4>Messages that look like the source</h4>")
        out.append(messages_html(supporting) if supporting else placeholder("No similar messages found."))
    return "".join(out)


def evidence_html(evidence: dict | None) -> str:
    if not evidence:
        return ""
    out = []
    calls = evidence.get("tool_calls") or []
    if calls:
        out.append(f"<p><b>Tools used ({len(calls)})</b></p><ol>")
        out += [f"<li><code>{esc(t.get('tool_name'))}</code> "
                f"<span style='{MUTED}'>{esc(json.dumps(t.get('tool_input', {}), ensure_ascii=False))}</span></li>"
                for t in calls]
        out.append("</ol>")
    concl = evidence.get("conclusions") or []
    out.append(f"<p><b>Conclusions read ({len(concl)})</b></p>")
    out.append("<ul>" + "".join(conclusion_line(c) for c in concl) + "</ul>" if concl else placeholder("None."))
    msgs = evidence.get("messages") or []
    if msgs:
        out.append(f"<p><b>Messages read ({len(msgs)})</b> <span style='{MUTED}'>"
                   + esc(", ".join(f"{m.get('peer_id')}@{m.get('session_id')}" for m in msgs[:20]))
                   + ("…" if len(msgs) > 20 else "") + "</span></p>")
    return "".join(out)


def answer_html(question: str, resp: dict, header: str = "") -> str:
    head = f"<p style='{MUTED}'>{esc(header)}</p>" if header else ""
    return (f"{head}<p><b>Q:</b> {esc(question)}</p>"
            f"<p><b>A:</b> {esc(resp.get('content'))}</p>"
            f"<p style='{MUTED}'>answered in {resp.get('elapsed_s', '?')} s</p>"
            + evidence_html(resp.get("evidence")) + "<hr>")


def diff_html(diff: dict, taken_at: str) -> str:
    out = [f"<h3>Changes since snapshot of {esc(short_time(taken_at))}</h3>",
           f"<p style='{MUTED}'>{len(diff['added'])} added · {len(diff['removed'])} removed · "
           f"{diff['kept']} unchanged</p>"]
    for title, rows in (("Added", diff["added"]), ("Removed", diff["removed"])):
        out.append(f"<h4>{title} ({len(rows)})</h4>")
        out.append("<ul>" + "".join(conclusion_line(c) for c in rows) + "</ul>" if rows else placeholder("None."))
    return "".join(out)


def comparison_report_html(data: dict, metrics: list[str]) -> str:
    """A standalone page for a saved Compare-tab result; the raw ``data`` is embedded as JSON for reloading."""
    key = next((k for k in ("peers", "levels") if k in data), "workspaces")  # what the columns are
    wss = list(data[key])
    subject = data.get("peer") or data.get("workspace") or ""
    title = f"Honcho comparison · {subject} · {short_time(data['saved_at'])}"
    question = (f"<p><b>Question:</b> {esc(data['question'])} "
                f"<span style='{MUTED}'>(reasoning {esc(data['reasoning'])})</span></p>" if data.get("question")
                else f"<p style='{MUTED}'><i>No question was asked.</i></p>")
    head = "".join(f"<th>{esc(ws)}</th>" for ws in wss)
    rows = "".join(f"<tr><th style='text-align:left'>{esc(m)}</th>"
                   + "".join(f"<td>{esc(data[key][ws]['counts'].get(m, ''))}</td>" for ws in wss) + "</tr>"
                   for m in metrics)
    sections = []
    for ws in wss:
        w = data[key][ws]
        answer = (answer_html(data["question"], w["answer"]) if w.get("answer")
                  else placeholder("No answer saved."))
        details = ""
        if "card" in w:  # tabs that load the peer's data (not the reasoning tab)
            details = (f"<details><summary>Peer card ({len(w.get('card') or [])} facts)</summary>"
                       f"{card_html(w.get('card'))}</details>"
                       f"<details><summary>Representation</summary>"
                       f"{text_html(w.get('representation'), 'No representation.')}</details>"
                       f"<details><summary>Conclusions ({len(w.get('conclusions') or [])})</summary>"
                       f"{conclusions_html(w.get('conclusions') or [], {})}</details>")
        sections.append(f"<h2>{esc(ws)}</h2>{answer}{details}")
    raw = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>"
            "<style>body{font-family:sans-serif;max-width:1100px;margin:auto;padding:16px}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}"
            "details{margin:6px 0}</style></head><body>"
            f"<h1>{esc(title)}</h1>{question}<table><tr><th></th>{head}</tr>{rows}</table>"
            + "".join(sections)
            + f'<script type="application/json" id="comparison-data">{raw}</script></body></html>')


def parse_comparison_html(text: str) -> dict | None:
    """The data embedded in a page written by ``comparison_report_html``; None if the page has none."""
    match = re.search(r'<script type="application/json" id="comparison-data">(.*?)</script>', text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def queue_text(status: dict) -> str:
    waiting, busy = status.get("pending_work_units", 0), status.get("in_progress_work_units", 0)
    return "idle" if not (waiting or busy) else f"{waiting} waiting · {busy} processing"
