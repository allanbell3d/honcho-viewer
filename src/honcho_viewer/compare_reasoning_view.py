"""Compare reasoning tab: ask ONE peer the same question at several reasoning levels, with time and token stats.

Time comes from the viewer itself (including time to the first streamed words). Token counts, server time and
LLM calls come from Honcho's own telemetry, received live by the listener in ``telemetry.py`` and matched to each
question by workspace, peer, reasoning level and arrival time.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSpinBox, QVBoxLayout)

from . import explain, render
from .client import REASONING_LEVELS
from .compare_panel import Column, ComparePanel, WorkspaceMixin
from .telemetry import summarize_run
from .telemetry_hub import DEFAULT_PORT
from .widgets import friendly_error, set_combo_items, tip

WAIT_FOR_TELEMETRY_S = 120

STATS = (
    ("Wall time (s)", "Time from sending the question until the whole answer had arrived, measured by this viewer."),
    ("First words after (s)", "Streaming: time until the first words appeared. High reasoning thinks a long time "
                              "before it starts to speak."),
    ("Server time (s)", "Honcho's own measurement of the whole run. From telemetry."),
    ("Input tokens", "Tokens sent to the model(s) in total, over all iterations. From telemetry."),
    ("  of which cached", "Part of the input served from the provider's prompt cache (cheaper). From telemetry."),
    ("Output tokens", "Tokens the model(s) produced, including tool calls. From telemetry."),
    ("Iterations", "How many reasoning rounds the agent ran. From telemetry."),
    ("Tool calls", "Searches and lookups the agent made (telemetry; otherwise counted from the evidence)."),
    ("LLM calls", "Individual model calls behind this answer. From telemetry."),
    ("Model(s)", "Models used for those calls. From telemetry."),
    ("Conclusions pre-loaded", "Conclusions handed to the agent before it started. From telemetry."),
    ("Conclusions read", "Conclusions the agent accessed (evidence). It lists what was read, not what was relied on."),
    ("Messages read", "Messages the agent accessed through its search tools (evidence)."),
    ("Answer words", "Length of the answer."),
)
TELEMETRY_ROWS = {"Server time (s)", "Input tokens", "  of which cached", "Output tokens", "Iterations", "LLM calls",
                  "Model(s)", "Conclusions pre-loaded"}


def _fmt(value) -> str:
    return "–" if value is None else f"{value:,}" if isinstance(value, int) else str(value)


class CompareReasoningView(WorkspaceMixin, ComparePanel):
    HINT = explain.TAB_HINTS["compare_reasoning"]
    ASK_PLACEHOLDER = "Ask the peer the same question at every ticked reasoning level…"
    FIRST_ROW = ("Reasoning level", "The reasoning_level sent to Honcho for this column.")
    REPORT_KEY = "levels"
    VIEWS = ("Stats", "Answers")
    TABLE_VIEW = "Stats"
    METRICS = STATS
    LOADS_DETAILS = False
    HAS_REASONING = False
    UI_KEY = "reasoning_workspace"

    _chunk = Signal(int, str, str)  # (ask serial, column key, text so far): emitted from the worker thread

    def __init__(self, ctx):
        self.stats: dict[str, dict] = {}
        self._claimed: set[str] = set()
        self._serial = 0
        super().__init__(ctx)
        self.evidence.setChecked(True)  # the Evidence stats need it
        self.evidence.hide()
        self._chunk.connect(self._on_chunk)
        self.ctx.telemetry.events_received.connect(self._on_telemetry)
        self.ctx.telemetry.state_changed.connect(self._update_listener)
        self._update_listener()

    # ---- selector
    def _build_selector(self, layout: QVBoxLayout) -> None:
        self._make_workspace_combo()
        self.peer_combo = tip(QComboBox(), "The peer that gets asked.")
        self.level_list = tip(QListWidget(), "Tick the reasoning levels to try. Each one is a separate question to "
                                             "Honcho (and a separate LLM run), so 'max' can be slow and costly.")
        for level in REASONING_LEVELS:
            item = QListWidgetItem(level)
            item.setData(Qt.UserRole, level)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.level_list.addItem(item)
        self.level_list.setMaximumHeight(120)

        self.port_spin = tip(QSpinBox(), "Port the listener waits on. Honcho's TELEMETRY_ENDPOINT must reach it.")
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self.ctx.store.ui_value("telemetry_port", DEFAULT_PORT)))
        self.key_edit = tip(QLineEdit(self.ctx.store.ui_value("telemetry_key", ""), placeholderText="optional"),
                            "Optional shared secret. If set, Honcho must send it in the "
                            "X-Telemetry-Key header, otherwise the post is rejected.")
        self.allowed_edit = tip(QLineEdit(self.ctx.store.ui_value("telemetry_allowed", ""),
                                          placeholderText="anyone (or e.g. 192.168.1.22)"),
                                "Only accept posts from these sender IP addresses (comma separated), e.g. your "
                                "Honcho server. Empty = anyone who can reach the port.")
        self.listen_btn = tip(QPushButton("Start listening"),
                              "Starts a small listener on this PC that receives Honcho's telemetry. It only "
                              "receives; it never talks back to Honcho.")
        self.listen_btn.clicked.connect(self._toggle_listener)
        self.copy_btn = tip(QPushButton("Copy Honcho settings"), "Copies the TELEMETRY_* lines to paste into "
                                                               "Honcho's environment.")
        self.copy_btn.clicked.connect(self._copy_settings)
        self.listen_status = QLabel()
        self.listen_status.setWordWrap(True)
        self.listen_status.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout.addWidget(QLabel("<b>Workspace</b>"))
        layout.addWidget(self.ws_combo)
        layout.addWidget(QLabel("<b>Peer</b>"))
        layout.addWidget(self.peer_combo)
        layout.addWidget(QLabel("<b>Reasoning levels</b>"))
        layout.addWidget(self.level_list)
        layout.addWidget(QLabel("<b>Telemetry (token counts)</b>"))
        layout.addWidget(self.listen_status)
        row = QVBoxLayout()
        row.addWidget(QLabel("Port"))
        row.addWidget(self.port_spin)
        row.addWidget(QLabel("Only accept from"))
        row.addWidget(self.allowed_edit)
        row.addWidget(QLabel("Secret"))
        row.addWidget(self.key_edit)
        layout.addLayout(row)
        layout.addWidget(self.listen_btn)
        layout.addWidget(self.copy_btn)
        layout.addStretch(1)

    def _clear_peers(self) -> None:
        set_combo_items(self.peer_combo, [])

    def _fill_peers(self, ids: list[str]) -> None:
        set_combo_items(self.peer_combo, [(p, p) for p in ids])

    def _relabel(self) -> None:
        self._relabel_workspaces()
        self._refresh()

    def checked_levels(self) -> list[str]:
        return [self.level_list.item(i).data(Qt.UserRole) for i in range(self.level_list.count())
                if self.level_list.item(i).checkState() == Qt.Checked]

    def set_levels(self, levels: list[str]) -> None:
        for i in range(self.level_list.count()):
            item = self.level_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in levels else Qt.Unchecked)

    # ---- hooks for ComparePanel
    def _columns(self) -> list[Column]:
        ws, peer = self.ws_combo.currentData(), self.peer_combo.currentData()
        return [Column(level, ws, peer, level) for level in self.checked_levels()] if ws and peer else []

    def _title(self, col: Column) -> str:
        return f"reasoning: {col.level}"

    def _first_cell(self, col: Column) -> str:
        return col.level or ""

    def _asked_reasoning(self, body: dict) -> str:
        return ", ".join(c.level for c in self._loaded if c.level)

    def _answer_header(self, col: Column, body: dict) -> str:
        return f"reasoning {col.level}"

    def _report_head(self) -> dict:
        first = self._loaded[0] if self._loaded else None
        return {"peer": first.peer if first else None, "workspace": first.ws if first else None}

    def _report_subject(self) -> str:
        return f"{self._loaded[0].peer}-reasoning" if self._loaded else "reasoning"

    def _report_column_extra(self, col: Column) -> dict:
        st = self.stats.get(col.key, {})
        return {"level": col.level, "stats": {k: v for k, v in st.items() if k in ("wall", "first", "words", "state")},
                "telemetry": st.get("telemetry"), "dialectic_event": st.get("dialectic"),
                "run_events": st.get("run_events")}

    def _restore_saved(self, data: dict) -> None:
        self.stats = {}
        for key, entry in data[self.REPORT_KEY].items():
            self.stats[key] = {**(entry.get("stats") or {}), "telemetry": entry.get("telemetry"),
                               "dialectic": entry.get("dialectic_event"), "run_events": entry.get("run_events"),
                               "evidence": (entry.get("answer") or {}).get("evidence") or {}}

    # ---- asking: one level after another, so the timings do not disturb each other
    def _start_asks(self, body: dict, ok) -> None:
        self._serial += 1
        serial, queue = self._serial, list(self._loaded)
        self.stats = {}
        self._claimed = set()
        for col in queue:
            self.stats[col.key] = {"state": "queued"}
            self.answers[col.key] = render.placeholder("Waiting for its turn…")

        def run(i: int) -> None:
            if i >= len(queue) or not ok():
                return
            col = queue[i]
            st = self.stats[col.key]
            st.update(state="running", t_start=time.time())
            self.answers[col.key] = render.placeholder("Honcho is thinking…")
            self._refresh()
            level_body = {**body, "reasoning_level": col.level, "include_evidence": True}
            last = [0.0]

            def on_text(text: str) -> None:  # worker thread: throttle, then hand over to the GUI thread
                now = time.monotonic()
                if now - last[0] > 0.15:
                    last[0] = now
                    self._chunk.emit(serial, col.key, text)

            def done(resp: dict) -> None:
                self._finish(col, body, resp)
                run(i + 1)

            def failed(exc: Exception) -> None:
                st.update(state="error")
                self.answers[col.key] = render.placeholder(f"Failed: {friendly_error(exc)}")
                self._refresh()
                run(i + 1)

            self.ctx.call(lambda c: c.chat_stream(col.ws, col.peer, level_body, on_text), done, ok, failed)

        run(0)

    def _on_chunk(self, serial: int, key: str, text: str) -> None:
        if serial == self._serial and self.stats.get(key, {}).get("state") == "running":
            self.answers[key] = (f"<p style='{render.MUTED}'>streaming…</p><p><b>A:</b> {render.esc(text)}</p>")
            self._refresh()

    def _finish(self, col: Column, body: dict, resp: dict) -> None:
        evidence = resp.get("evidence") or {}
        self.stats[col.key].update(
            state="done", wall=resp.get("elapsed_s"), first=resp.get("first_token_s"),
            words=len((resp.get("content") or "").split()), evidence=evidence, t_end=time.time())
        self._match(col)
        self._set_answer(col, body, resp)
        QTimer.singleShot((WAIT_FOR_TELEMETRY_S + 1) * 1000, self._refresh)  # flips "waiting…" to "not received"

    # ---- telemetry
    def _match(self, col: Column) -> None:
        st = self.stats.get(col.key)
        if not st or st.get("state") != "done":
            return
        store = self.ctx.telemetry.store
        event = st.get("dialectic")
        if event is None:
            event = store.find_dialectic(col.ws, col.peer, col.level, since=st["t_start"] - 1, claimed=self._claimed)
            if event is None:
                return
            st["dialectic"] = event
            self._claimed.add(event["data"]["run_id"])
        children = store.run_events(event["data"]["run_id"])
        st["telemetry"] = summarize_run(event, children)
        st["run_events"] = children

    def _on_telemetry(self, _count: int = 0) -> None:
        for col in self._loaded:
            self._match(col)
        self._update_listener()
        self._refresh()

    def _telemetry_cell(self, st: dict, key: str) -> str:
        telemetry = st.get("telemetry")
        if telemetry is not None:
            return _fmt({"Server time (s)": telemetry["server_s"], "Input tokens": telemetry["input_tokens"],
                         "  of which cached": telemetry["cache_read_tokens"],
                         "Output tokens": telemetry["output_tokens"], "Iterations": telemetry["iterations"],
                         "LLM calls": telemetry["llm_calls"], "Conclusions pre-loaded": telemetry["prefetched"],
                         "Model(s)": ", ".join(telemetry["models"]) or None}[key])
        if st.get("state") != "done":
            return "…"
        if not self.ctx.telemetry.running:
            return "listener off"
        return "waiting…" if time.time() - st["t_end"] < WAIT_FOR_TELEMETRY_S else "not received"

    def _live_counts(self, col: Column) -> list[str]:
        st = self.stats.get(col.key, {})
        evidence = st.get("evidence") or {}
        telemetry = st.get("telemetry") or {}
        calls = telemetry.get("tool_calls")
        if calls is None and st.get("state") == "done":
            calls = len(evidence.get("tool_calls") or [])
        waiting = st.get("state") in (None, "queued", "running")
        own = {"Wall time (s)": st.get("wall"), "First words after (s)": st.get("first"),
               "Tool calls": calls, "Conclusions read": len(evidence.get("conclusions") or []) if evidence else None,
               "Messages read": len(evidence.get("messages") or []) if evidence else None,
               "Answer words": st.get("words")}
        cells = [self._first_cell(col)]
        for name, _ in STATS:
            if name in TELEMETRY_ROWS:
                cells.append(self._telemetry_cell(st, name))
            elif name == "Tool calls" and telemetry.get("tool_calls") is not None:
                cells.append(_fmt(telemetry["tool_calls"]))
            else:
                cells.append("…" if waiting else _fmt(own.get(name)))
        return cells

    # ---- listener controls
    def _toggle_listener(self) -> None:
        hub = self.ctx.telemetry
        if hub.running:
            hub.stop()
            return
        try:
            hub.start(self.port_spin.value(), self.key_edit.text(), self.allowed_edit.text())
        except OSError as exc:
            self.ctx.status.emit(f"Cannot listen on port {self.port_spin.value()}: {exc}. Try another port.")

    def _settings_text(self) -> str:
        hub = self.ctx.telemetry
        lines = ["TELEMETRY_ENABLED=true", f"TELEMETRY_ENDPOINT={hub.url()}"]
        if hub.key:
            lines.append('TELEMETRY_HEADERS={"X-Telemetry-Key": "' + hub.key + '"}')
        return "\n".join(lines)

    def _copy_settings(self) -> None:
        QGuiApplication.clipboard().setText(self._settings_text())
        self.ctx.status.emit("Copied. Paste these into Honcho's environment and restart Honcho.")

    def _update_listener(self) -> None:
        hub = self.ctx.telemetry
        self.listen_btn.setText("Stop listening" if hub.running else "Start listening")
        self.port_spin.setEnabled(not hub.running)
        self.key_edit.setEnabled(not hub.running)
        self.allowed_edit.setEnabled(not hub.running)
        self.copy_btn.setEnabled(hub.running)
        if hub.running:
            note = f" Cannot write the log: {hub.receiver.last_error}" if hub.receiver.last_error else ""
            self.listen_status.setText(
                f"Listening on {hub.url()}<br>{hub.store.total} events received. Everything is logged in "
                f"local/telemetry/.{note}")
        else:
            self.listen_status.setText("Not listening. Start it, then point Honcho's TELEMETRY_ENDPOINT here "
                                       "to see real token counts.")
