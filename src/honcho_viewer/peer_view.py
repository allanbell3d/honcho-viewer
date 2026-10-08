"""The Explore pane for one peer: Overview, Conclusions, Messages and Ask tabs."""
from __future__ import annotations

import json
import time
from html import escape

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from . import explain, render
from .ask import build_chat_body
from .async_call import Latest
from .client import CONCLUSION_LEVELS, REASONING_LEVELS, REPRESENTATION_MAX, HonchoError
from .store import diff_conclusions
from .widgets import AppContext, HtmlView, hint, set_combo_items, tip


def _group(title: str, widget: QWidget, tooltip: str = "") -> QGroupBox:
    box = QGroupBox(title)
    box.setToolTip(tooltip)
    QVBoxLayout(box).addWidget(widget)
    return box


def _peer_items(peers: list[str], first: str, exclude: str | None = None) -> list[tuple[str, str | None]]:
    return [(first, None)] + [(p, p) for p in peers if p != exclude]


class _PeerTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.latest = Latest()
        self.ws: str | None = None
        self.peer: str | None = None
        self.peers: list[str] = []

    def set_peer(self, ws: str, peer: str, peers: list[str]) -> None:
        self.ws, self.peer, self.peers = ws, peer, peers
        self.load()

    def load(self) -> None:
        pass


# ---- Overview ---------------------------------------------------------------
class OverviewTab(_PeerTab):
    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.about_combo = tip(QComboBox(), "Whose memory to show. 'itself' = what Honcho knows about this peer. "
                                            "Another peer = what THIS peer knows about that one.")
        self.about_combo.currentIndexChanged.connect(self.load)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.load)
        self.card_view = HtmlView()
        self.rep_view = HtmlView()
        self.rep_note = tip(QLabel(), f"Honcho never returns the whole memory here: at most {REPRESENTATION_MAX} "
                                      "conclusions, newest first. Without asking, the server sends only 25.")

        top = QHBoxLayout()
        top.addWidget(QLabel("About:"))
        top.addWidget(self.about_combo)
        top.addWidget(self.rep_note, 1)
        top.addWidget(refresh)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(_group("Peer card", self.card_view,
                               "A short list of stable facts (name, job, preferences…) kept up to date by Honcho."))
        split.addWidget(_group("Representation", self.rep_view,
                               f"An excerpt of the peer's conclusions: up to {REPRESENTATION_MAX}, newest first. "
                               "The complete list is in the Conclusions tab."))
        split.setSizes([320, 640])
        layout = QVBoxLayout(self)
        layout.addWidget(hint(explain.TAB_HINTS["overview"]))
        layout.addLayout(top)
        layout.addWidget(split)

    def set_peer(self, ws: str, peer: str, peers: list[str]) -> None:
        set_combo_items(self.about_combo, _peer_items(peers, "itself", exclude=peer), keep_current=False)
        super().set_peer(ws, peer, peers)

    def load(self) -> None:
        if not self.ws:
            return
        ws, peer, target = self.ws, self.peer, self.about_combo.currentData()
        ok = self.latest.ticket()
        self.card_view.setHtml(render.placeholder("Loading…"))
        self.rep_view.setHtml(render.placeholder("Loading…"))
        self.rep_note.setText("")
        self.ctx.call(lambda c: c.peer_card(ws, peer, target),
                      lambda card: self.card_view.setHtml(render.card_html(card)), ok)
        self.ctx.call(lambda c: c.representation(ws, peer, target),
                      lambda rep: self.rep_view.setHtml(render.text_html(rep, "No representation yet.")), ok)
        self.ctx.call(lambda c: c.count_conclusions(ws, observer=peer, observed=target or peer),
                      lambda n: self.rep_note.setText(f"Showing up to {REPRESENTATION_MAX} of {n:,} conclusions "
                                                      "(newest first) · all of them are in the Conclusions tab"), ok)


# ---- Conclusions ------------------------------------------------------------
COLUMNS = ("", "Level", "Conclusion", "By → about", "Session", "Created")


class ConclusionsTab(_PeerTab):
    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.rows: list[dict] = []
        self._detail: dict | None = None

        self.observer_combo = tip(QComboBox(), "By (observer): whose memory this is. Default: the peer itself (Honcho's own "
                                               "view). Agents with 'observe others' keep their own copy of every fact "
                                               "about a peer, so '(anyone)' shows each fact twice.")
        self.about_combo = tip(QComboBox(), "About (observed): which peer the conclusions describe.")
        self.level_combo = tip(QComboBox(), "Filter by how the conclusion was made. Hover the options.")
        self.level_combo.addItem("(all levels)", None)
        for level in CONCLUSION_LEVELS:
            self.level_combo.addItem(f"{level} – {explain.LEVELS[level][0]}", level)
            self.level_combo.setItemData(self.level_combo.count() - 1, explain.LEVELS[level][1], Qt.ToolTipRole)
        for combo in (self.observer_combo, self.about_combo, self.level_combo):
            combo.currentIndexChanged.connect(self.load)
        self.text_filter = QLineEdit(placeholderText="Filter text…")
        self.text_filter.textChanged.connect(self._fill_table)
        self.unrated_only = tip(QCheckBox("Unrated only"), "Hide conclusions you already rated.")
        self.unrated_only.toggled.connect(self._fill_table)
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self.load)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        for col in range(len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)

        rate_row = QHBoxLayout()
        for key, rating, text in (("1", "correct", "✓ Correct"), ("2", "wrong", "× Wrong"),
                                  ("3", "unsure", "? Unsure"), ("0", None, "Clear")):
            btn = tip(QPushButton(f"{text} ({key})"), explain.RATINGS[rating])
            btn.clicked.connect(lambda _=False, r=rating: self.rate(r))
            rate_row.addWidget(btn)
            shortcut = QShortcut(QKeySequence(key), self.table)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda r=rating: self.rate(r))
        self.summary_label = QLabel()
        rate_row.addWidget(self.summary_label, 1)

        self.detail = HtmlView()
        self.support_btn = tip(QPushButton("Find supporting messages"), explain.SUPPORT)
        self.support_btn.clicked.connect(self._find_support)
        self.snapshot_btn = tip(QPushButton("📸 Snapshot"), explain.SNAPSHOT)
        self.snapshot_btn.clicked.connect(self._snapshot)
        self.load_snapshot_btn = tip(QPushButton("Load snapshot…"), explain.LOAD_SNAPSHOT)
        self.load_snapshot_btn.clicked.connect(self._load_snapshot)
        self._chosen: dict | None = None  # a snapshot saved or loaded just now, for self._chosen_for
        self._chosen_for: tuple | None = None
        self.diff_btn = tip(QPushButton("What changed?"), explain.SNAPSHOT)
        self.diff_btn.clicked.connect(self._diff)
        self.snapshot_label = QLabel()

        filters = QHBoxLayout()
        for label, widget in (("By:", self.observer_combo), ("About:", self.about_combo), ("Level:", self.level_combo)):
            filters.addWidget(QLabel(label))
            filters.addWidget(widget)
        filters.addWidget(self.text_filter, 1)
        filters.addWidget(self.unrated_only)
        filters.addWidget(reload_btn)

        detail_box = QWidget()
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addLayout(rate_row)
        detail_layout.addWidget(self.detail)
        tools = QHBoxLayout()
        tools.addWidget(self.support_btn)
        tools.addStretch()
        tools.addWidget(self.snapshot_label)
        tools.addWidget(self.load_snapshot_btn)
        tools.addWidget(self.snapshot_btn)
        tools.addWidget(self.diff_btn)
        detail_layout.addLayout(tools)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(detail_box)
        split.setSizes([420, 300])
        layout = QVBoxLayout(self)
        layout.addWidget(hint(explain.TAB_HINTS["conclusions"]))
        layout.addLayout(filters)
        layout.addWidget(split)

    def set_peer(self, ws: str, peer: str, peers: list[str]) -> None:
        set_combo_items(self.observer_combo, _peer_items(peers, "(anyone)"), keep_current=False)
        set_combo_items(self.about_combo, [(p, p) for p in peers], keep_current=False)
        for combo in (self.observer_combo, self.about_combo):  # start on the peer's own view: By = About = peer
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(peer)))
            combo.blockSignals(False)
        super().set_peer(ws, peer, peers)

    # -- loading & table
    def load(self) -> None:
        if not self.ws:
            return
        ws = self.ws
        observer, observed = self.observer_combo.currentData(), self.about_combo.currentData()
        level = self.level_combo.currentData()
        self.summary_label.setText("Loading…")
        self._update_snapshot_label()
        self.ctx.call(lambda c: c.list_conclusions(ws, observer, observed, level), self._show,
                      self.latest.ticket("list"))

    def _show(self, rows: list[dict]) -> None:
        self.rows = rows
        self._fill_table()
        self._detail = None
        self.detail.setHtml(render.placeholder("Select a conclusion to see where it came from."))

    def _visible_rows(self) -> list[dict]:
        text = self.text_filter.text().strip().lower()
        unrated = self.unrated_only.isChecked()
        return [c for c in self.rows
                if (not text or text in (c.get("content") or "").lower())
                and not (unrated and self.ctx.store.rating(self.ws, c["id"]))]

    def _mark_item(self, c: dict) -> QTableWidgetItem:
        rating = self.ctx.store.rating(self.ws, c["id"])
        mark, color = render.RATING_MARKS.get(rating, ("", "#888"))
        item = QTableWidgetItem(mark)
        item.setForeground(QColor(color))
        item.setData(Qt.UserRole, c)
        return item

    def _fill_table(self) -> None:
        rows = self._visible_rows()
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, c in enumerate(rows):
            level = c.get("level", "")
            self.table.setItem(r, 0, self._mark_item(c))
            cells = (explain.LEVELS.get(level, (level,))[0], c.get("content") or "",
                     f"{c.get('observer_id')} → {c.get('observed_id')}", c.get("session_id") or "–",
                     render.short_time(c.get("created_at")))
            for col, text in enumerate(cells, start=1):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(QColor(render.LEVEL_COLORS.get(level, "#888")))
                    item.setToolTip(explain.LEVELS.get(level, ("", ""))[1])
                elif col == 2:
                    item.setToolTip(text)
                self.table.setItem(r, col, item)
        self.table.blockSignals(False)
        self._update_summary()

    def _update_summary(self) -> None:
        n = len(self.rows)
        from_messages = sum(1 for c in self.rows if c.get("level") == "explicit")
        acc = self.ctx.store.accuracy(self.ws, ids={c["id"] for c in self.rows})
        pct = f"{acc.percent}%" if acc.percent is not None else "–"
        self.summary_label.setText(
            f"{n:,} conclusions: {from_messages:,} from messages, {n - from_messages:,} from dreaming  ·  "
            f"rated {acc.rated} (✓{acc.correct} ×{acc.wrong} ?{acc.unsure})  ·  accuracy {pct}")

    def current_conclusion(self) -> dict | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

    # -- rating
    def rate(self, rating: str | None) -> None:
        c = self.current_conclusion()
        if not c:
            return
        row = self.table.currentRow()
        self.ctx.store.set_rating(self.ws, c, rating)
        if self.unrated_only.isChecked():
            self._fill_table()
            next_row = min(row, self.table.rowCount() - 1)
        else:
            self.table.setItem(row, 0, self._mark_item(c))
            self._update_summary()
            next_row = min(row + 1, self.table.rowCount() - 1)
        if next_row >= 0:
            self.table.selectRow(next_row)

    # -- detail: premises & supporting messages
    def _on_select(self) -> None:
        c = self.current_conclusion()
        if not c:
            return
        self._detail = {"c": c, "premises": None if c.get("source_ids") else [], "supporting": None}
        self.latest.ticket("support")  # drop a search still running for the previous row
        self._render_detail()
        if not c.get("source_ids"):
            return
        ws, ids, detail = self.ws, c["source_ids"][:20], self._detail

        def fetch(client):
            found = []
            for cid in ids:
                try:
                    found.append(client.get_conclusion(ws, cid))
                except HonchoError as exc:
                    if exc.status != 404:  # a premise can be deleted later
                        raise
            return found

        def done(premises):
            detail["premises"] = premises
            self._render_detail()

        self.ctx.call(fetch, done, self.latest.ticket("premises"))

    def _render_detail(self) -> None:
        d = self._detail
        if d:
            self.detail.setHtml(render.conclusion_detail_html(d["c"], d["premises"], d["supporting"]))

    def _find_support(self) -> None:
        if not self._detail:
            self.ctx.status.emit("Select a conclusion first.")
            return
        ws, detail = self.ws, self._detail
        c = detail["c"]

        def done(messages):
            detail["supporting"] = messages
            self._render_detail()

        self.ctx.status.emit("Searching messages…")
        self.ctx.call(lambda cl: cl.search_messages(ws, c.get("content") or "", session_id=c.get("session_id"),
                                                    peer_id=c.get("observed_id")),
                      done, self.latest.ticket("support"))

    # -- snapshots (before / after dreaming)
    def _current_snapshot(self) -> dict | None:
        """The 'before' for What changed?: the one saved/loaded here this session, else the newest saved one."""
        if not self.ws:
            return None
        if self._chosen is not None and self._chosen_for == (self.ws, self.peer):
            return self._chosen
        return self.ctx.store.load_snapshot(self.ws, self.peer)

    def _update_snapshot_label(self) -> None:
        snap = self._current_snapshot()
        self.snapshot_label.setText(
            f"Snapshot {render.short_time(snap['taken_at'])} · {len(snap['conclusions'])} conclusions"
            if snap else "No snapshot yet")

    def _snapshot(self) -> None:
        if not self.ws:
            return
        ws, peer = self.ws, self.peer

        def done(rows):
            default = self.ctx.store.snapshot_default_path(ws, peer)
            path = self.ctx.pick_save_path(self, "Save snapshot", default, "Snapshot (*.json)", "snapshots")
            if path is None:
                self.ctx.status.emit("Snapshot not saved.")
                return
            self.ctx.store.write_snapshot(path, ws, peer, rows)
            self._chosen, self._chosen_for = self.ctx.store.read_snapshot(path), (ws, peer)
            self._update_snapshot_label()
            self.ctx.status.emit(f"Snapshot saved: {len(rows)} conclusions about {peer} in {ws} -> {path}")

        self.ctx.call(lambda c: c.list_conclusions(ws, observer=peer, observed=peer), done,
                      self.latest.ticket("snapshot"))

    def _load_snapshot(self) -> None:
        if not self.ws:
            return
        path = self.ctx.pick_open_path(self, "Load snapshot", self.ctx.store.root / "snapshots",
                                       "Snapshot (*.json)", "snapshots")
        if path is None:
            return
        snap = self.ctx.store.read_snapshot(path)
        if snap is None:
            self.ctx.status.emit(f"{path.name} is not a snapshot file.")
            return
        if snap.get("workspace", self.ws) != self.ws or snap.get("peer", self.peer) != self.peer:
            self.ctx.status.emit(f"That snapshot is of {snap.get('peer')} in {snap.get('workspace')}: select that "
                                 "peer first.")
            return
        self._chosen, self._chosen_for = snap, (self.ws, self.peer)
        self._update_snapshot_label()
        self.ctx.status.emit(f"Loaded snapshot {path.name}. 'What changed?' now compares against it.")

    def _diff(self) -> None:
        if not self.ws:
            return
        ws, peer = self.ws, self.peer
        snap = self._current_snapshot()
        if not snap:
            self.detail.setHtml(render.placeholder(
                "No snapshot yet. Click Snapshot now, then come back after Honcho has dreamed or "
                "processed more messages."))
            return
        self._detail = None
        # 0.0.1 snapshots mixed in other observers' copies; only compare the peer's own view
        before = [c for c in snap["conclusions"] if c.get("observer_id", peer) == c.get("observed_id", peer)]
        self.ctx.call(lambda c: c.list_conclusions(ws, observer=peer, observed=peer),
                      lambda rows: self.detail.setHtml(
                          render.diff_html(diff_conclusions(before, rows), snap["taken_at"])),
                      self.latest.ticket("snapshot"))


# ---- Messages ---------------------------------------------------------------
class MessagesTab(_PeerTab):
    sessions_loaded = Signal(list)

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self._session: str | None = None
        self._messages: list[dict] = []
        self._page = 0
        self.session_list = tip(QListWidget(), "Sessions (conversations) this peer took part in.")
        self.session_list.currentItemChanged.connect(self._open_session)
        self.view = HtmlView()
        self.count_label = QLabel()
        self.more_btn = QPushButton("Load 50 more")
        self.more_btn.setEnabled(False)
        self.more_btn.clicked.connect(self._fetch)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.view)
        row = QHBoxLayout()
        row.addWidget(self.count_label)
        row.addStretch()
        row.addWidget(self.more_btn)
        right_layout.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.session_list)
        split.addWidget(right)
        split.setSizes([200, 760])
        layout = QVBoxLayout(self)
        layout.addWidget(hint(explain.TAB_HINTS["messages"]))
        layout.addWidget(split)

    def load(self) -> None:
        if not self.ws:
            return
        ws, peer = self.ws, self.peer
        self.session_list.clear()
        self.view.setHtml(render.placeholder("Loading sessions…"))
        self.ctx.call(lambda c: c.peer_sessions(ws, peer), self._show_sessions, self.latest.ticket("sessions"))

    def _show_sessions(self, sessions: list[dict]) -> None:
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for s in sessions:
            item = QListWidgetItem(f"{s['id']}   {render.short_time(s.get('created_at'))}")
            item.setData(Qt.UserRole, s["id"])
            item.setToolTip(json.dumps({k: s.get(k) for k in ("metadata", "configuration", "is_active")},
                                       indent=2, ensure_ascii=False))
            self.session_list.addItem(item)
        self.session_list.blockSignals(False)
        self.sessions_loaded.emit([s["id"] for s in sessions])
        if sessions:
            self.session_list.setCurrentRow(0)
        else:
            self.view.setHtml(render.placeholder("This peer has no sessions."))

    def _open_session(self, current, _previous=None) -> None:
        if current is None:
            return
        self._session, self._messages, self._page = current.data(Qt.UserRole), [], 0
        self._fetch()

    def _fetch(self) -> None:
        ws, sid, page = self.ws, self._session, self._page + 1
        self.more_btn.setEnabled(False)
        self.ctx.call(lambda c: c.messages_page(ws, sid, page), self._add_page, self.latest.ticket("messages"))

    def _add_page(self, data: dict) -> None:
        self._page = data.get("page", self._page + 1)
        self._messages += data.get("items", [])
        self.view.setHtml(render.messages_html(self._messages))
        self.count_label.setText(f"{len(self._messages)} of {data.get('total', '?')} messages")
        self.more_btn.setEnabled(self._page < (data.get("pages") or 1))


# ---- Ask ----------------------------------------------------------------------
class AskTab(_PeerTab):
    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self._busy_since: float | None = None
        self._history: list[str] = []
        opts = explain.ASK_OPTIONS

        self.question = tip(QPlainTextEdit(), opts["query"])
        self.question.setPlaceholderText("e.g. What does this person do for a living?")
        self.question.setMaximumHeight(90)
        self.reasoning = tip(QComboBox(), opts["reasoning_level"])
        self.reasoning.addItems(REASONING_LEVELS)
        self.reasoning.setCurrentText("low")
        self.evidence = tip(QCheckBox("show what Honcho read"), opts["include_evidence"])
        self.evidence.setChecked(True)
        self.about_combo = tip(QComboBox(), opts["target"])
        self.session_combo = tip(QComboBox(), opts["session_id"])
        self.session_combo.addItem("(all sessions)", None)
        self.scope_edit = tip(QLineEdit(placeholderText="(none)"), opts["scope"])
        self.format_edit = tip(QPlainTextEdit(), opts["response_format"])
        self.format_edit.setPlaceholderText('(none)  e.g. {"type": "object", "properties": {"city": {"type": "string"}}}')
        self.format_edit.setMaximumHeight(60)
        self.preview = tip(QPlainTextEdit(), opts["preview"])
        self.preview.setReadOnly(True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #eb5757;")
        self.ask_btn = QPushButton("Ask  (Ctrl+Enter)")
        self.ask_btn.clicked.connect(self._ask)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self._ask)
        self.busy_label = QLabel()
        self.history = HtmlView(render.placeholder("Answers appear here, newest first."))
        self._timer = QTimer(self, interval=1000)
        self._timer.timeout.connect(self._tick)

        self.question.textChanged.connect(self._update_preview)
        self.reasoning.currentIndexChanged.connect(self._update_preview)
        self.evidence.toggled.connect(self._update_preview)
        self.about_combo.currentIndexChanged.connect(self._update_preview)
        self.session_combo.currentIndexChanged.connect(self._update_preview)
        self.scope_edit.textChanged.connect(self._update_preview)
        self.format_edit.textChanged.connect(self._update_preview)

        form = QFormLayout()
        for label, widget, key in (("Question", self.question, "query"),
                                   ("Reasoning level", self.reasoning, "reasoning_level"),
                                   ("Evidence", self.evidence, "include_evidence"),
                                   ("About (target)", self.about_combo, "target"),
                                   ("Session", self.session_combo, "session_id"),
                                   ("Scope", self.scope_edit, "scope"),
                                   ("Response format", self.format_edit, "response_format")):
            form.addRow(tip(QLabel(label), opts[key]), widget)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(form)
        left_layout.addWidget(tip(QLabel("Request that will be sent:"), opts["preview"]))
        left_layout.addWidget(self.preview, 1)
        left_layout.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addWidget(self.ask_btn)
        row.addWidget(self.busy_label, 1)
        left_layout.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.history)
        split.setSizes([420, 560])
        layout = QVBoxLayout(self)
        layout.addWidget(hint(explain.TAB_HINTS["ask"]))
        layout.addWidget(split)
        self._update_preview()

    def set_peer(self, ws: str, peer: str, peers: list[str]) -> None:
        set_combo_items(self.about_combo, _peer_items(peers, "itself", exclude=peer), keep_current=False)
        set_combo_items(self.session_combo, [("(all sessions)", None)], keep_current=False)
        self.ws, self.peer, self.peers = ws, peer, peers
        self._update_preview()

    def set_sessions(self, session_ids: list[str]) -> None:
        set_combo_items(self.session_combo, [("(all sessions)", None)] + [(s, s) for s in session_ids])
        self._update_preview()

    def _body(self) -> tuple[dict, str | None]:
        return build_chat_body(self.question.toPlainText(), self.reasoning.currentText(), self.evidence.isChecked(),
                               session_id=self.session_combo.currentData(), target=self.about_combo.currentData(),
                               scope=self.scope_edit.text(), response_format=self.format_edit.toPlainText())

    def _update_preview(self) -> None:
        body, error = self._body()
        path = f"POST /v3/workspaces/{self.ws or '…'}/peers/{self.peer or '…'}/chat"
        self.preview.setPlainText(path + "\n" + json.dumps(body, indent=2, ensure_ascii=False))
        self.error_label.setText(error or "")
        self.ask_btn.setEnabled(error is None and self.ws is not None and self._busy_since is None)

    def _tick(self) -> None:
        if self._busy_since is not None:
            self.busy_label.setText(f"Honcho is thinking… {int(time.monotonic() - self._busy_since)} s")

    def _set_busy(self, busy: bool) -> None:
        self._busy_since = time.monotonic() if busy else None
        self._timer.start() if busy else self._timer.stop()
        self.busy_label.setText("Honcho is thinking… 0 s" if busy else "")
        self._update_preview()

    def _ask(self) -> None:
        body, error = self._body()
        if error or self.ws is None or self._busy_since is not None:
            return
        ws, peer = self.ws, self.peer
        about = f" about {body['target']}" if body.get("target") else ""
        header = f"{ws} · {peer}{about} · reasoning {body['reasoning_level']}"

        def done(resp):
            self._set_busy(False)
            self._history.insert(0, render.answer_html(body["query"], resp, header))
            self.history.setHtml("".join(self._history))

        self._set_busy(True)
        self.ctx.call(lambda c: c.chat(ws, peer, body), done, on_error=lambda exc: self._set_busy(False))


# ---- the pane -----------------------------------------------------------------
class PeerView(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.header = QLabel()
        self.overview = OverviewTab(ctx)
        self.conclusions = ConclusionsTab(ctx)
        self.messages = MessagesTab(ctx)
        self.ask = AskTab(ctx)
        self.messages.sessions_loaded.connect(self.ask.set_sessions)
        self.tabs = QTabWidget()
        for widget, title, key in ((self.overview, "Overview", "overview"),
                                   (self.conclusions, "Conclusions", "conclusions"),
                                   (self.messages, "Messages", "messages"), (self.ask, "Ask", "ask")):
            self.tabs.addTab(widget, title)
            self.tabs.setTabToolTip(self.tabs.count() - 1, explain.TAB_HINTS[key])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.header)
        layout.addWidget(self.tabs)
        self.clear()

    def clear(self) -> None:
        self.header.setText("<i>Select a workspace, then a peer, on the left.</i>")
        self.tabs.setEnabled(False)

    def set_peer(self, ws: str, peer: str, peers: list[str]) -> None:
        self.header.setText(f"<b>{escape(peer)}</b> in <b>{escape(self.ctx.ws_title(ws))}</b>")
        self.tabs.setEnabled(True)
        for tab in (self.overview, self.conclusions, self.messages, self.ask):
            tab.set_peer(ws, peer, peers)
