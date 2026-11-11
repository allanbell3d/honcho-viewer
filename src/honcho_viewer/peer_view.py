"""The Explore pane for one peer: Overview, Conclusions, Messages and Ask tabs."""
from __future__ import annotations

import json
import time
from html import escape

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from . import explain, render
from .ask import build_chat_body
from .async_call import Latest
from .client import CONCLUSION_LEVELS, REASONING_LEVELS, REPRESENTATION_MAX, HonchoError
from .store import diff_conclusions
from .widgets import AppContext, HtmlView, copyable, hint, set_combo_items, tip

MORE_STEP = 500  # conclusions added per "Show more" click (a multiple of the client's PAGE_SIZE)


def _group(title: str, widget: QWidget, tooltip: str = "") -> QGroupBox:
    box = QGroupBox(title)
    box.setToolTip(tooltip)
    QVBoxLayout(box).addWidget(widget)
    return box


def _peer_items(peers: list[str], first: str, exclude: str | None = None) -> list[tuple[str, str | None]]:
    return [(first, None)] + [(p, p) for p in peers if p != exclude]


class _Progress(QObject):
    """Carries partial results from a worker thread to the GUI thread (queued signal)."""
    chunk = Signal(object)


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
        self.more_btn = tip(QPushButton(f"Show {MORE_STEP} more"), "Adds the next older conclusions below, as plain text.")
        self.more_btn.clicked.connect(self._more)
        self.card_view = HtmlView()
        self.rep_view = HtmlView()
        self.rep_note = tip(QLabel(), f"Honcho never returns the whole memory here: at most {REPRESENTATION_MAX} "
                                      "conclusions, newest first. Without asking, the server sends only 25.")

        top = QHBoxLayout()
        top.addWidget(QLabel("About:"))
        top.addWidget(self.about_combo)
        top.addWidget(self.rep_note, 1)
        top.addWidget(self.more_btn)
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
        # wide enough for the longest peer name (the list used to cut names short, e.g. "Pi-...tor")
        widest = max(self.about_combo.fontMetrics().horizontalAdvance(self.about_combo.itemText(i))
                     for i in range(self.about_combo.count()))
        self.about_combo.view().setMinimumWidth(widest + 40)
        self.about_combo.setMinimumWidth(int(self.about_combo.sizeHint().width() * 1.5))
        super().set_peer(ws, peer, peers)

    def load(self) -> None:
        if not self.ws:
            return
        ws, peer, target = self.ws, self.peer, self.about_combo.currentData()
        ok = self.latest.ticket()
        self._shown = REPRESENTATION_MAX
        self.more_btn.setEnabled(True)
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


    def _more(self) -> None:
        """Append the next MORE_STEP conclusions (newest first after those shown) as dated lines."""
        ws, peer, target, skip = self.ws, self.peer, self.about_combo.currentData() or self.peer, self._shown
        self.more_btn.setEnabled(False)
        ok = self.latest.ticket()

        def show(items: list[dict]) -> None:
            self._shown += MORE_STEP
            self.more_btn.setEnabled(len(items) == MORE_STEP)
            lines = [f"[{str(c.get('created_at', ''))[:19].replace('T', ' ')}] {c.get('content', '')}" for c in items]
            cursor = self.rep_view.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(f"\n\n## Conclusions {skip + 1:,}–{skip + len(items):,}\n\n" + "\n".join(lines))
        self.ctx.call(lambda c: c.conclusions_after(ws, peer, target, skip, MORE_STEP), show, ok)


# ---- Conclusions ------------------------------------------------------------
COLUMNS = ("", "Level", "Conclusion", "By → about", "Session", "Created")
CONCLUSION_COL = 2
MIN_CONCLUSION_WIDTH = 200


class ConclusionsTab(_PeerTab):
    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self.rows: list[dict] = []
        self._detail: dict | None = None
        self._loading = False  # a streamed load is still adding rows
        self._progress = _Progress()
        self._progress.chunk.connect(self._on_chunk)

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
        self.wrap_check = tip(QCheckBox("Wrap text"), "Show long conclusions in full, on several lines. Untick for "
                                                      "one line each (the full text is in the tooltip and below).")
        self.wrap_check.setChecked(bool(ctx.store.ui_value("conclusions_wrap", True)))
        self.wrap_check.toggled.connect(self._wrap_toggled)
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self.load)

        self._sort: tuple[int, Qt.SortOrder] | None = None  # None = the order Honcho returned
        self._fitting = False
        self._widths_set_by_hand = False
        self._rows_timer = QTimer(self, singleShot=True, interval=150)  # re-measure wrapped rows after a resize
        self._rows_timer.timeout.connect(self._fit_rows)
        self.table = QTableWidget(0, len(COLUMNS))
        copyable(self.table)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(self.wrap_check.isChecked())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)  # drag a column edge to resize it
        header.setSectionsMovable(True)  # drag a column header to rearrange the table
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)  # until a header is clicked
        self._restore_order()
        header.sectionMoved.connect(self._save_order)
        header.sectionClicked.connect(self._sort_clicked)
        header.sectionResized.connect(self._column_resized)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        self.table.viewport().installEventFilter(self)  # keep the Conclusion column filling the width
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
        filters.addWidget(self.wrap_check)
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
        """Stream the conclusions: the first page is on screen at once, the rest arrive in the background
        (STREAM_CHUNK_PAGES at a time) and are appended; the summary shows the progress. Changing the
        filters or the peer abandons the running load (its remaining pages are never requested)."""
        if not self.ws:
            return
        ws = self.ws
        observer, observed = self.observer_combo.currentData(), self.about_combo.currentData()
        level = self.level_combo.currentData()
        self.rows, self._detail, self._loading = [], None, True
        self._fill_table()
        self.summary_label.setText("Loading…")
        self.detail.setHtml(render.placeholder("Select a conclusion to see where it came from."))
        self._update_snapshot_label()
        current = self.latest.ticket("list")
        progress = self._progress

        def fetch(c):
            for items, total, done, pages in c.stream_conclusions(ws, observer, observed, level, current):
                progress.chunk.emit((current, items, total, done, pages))

        self.ctx.call(fetch, lambda _r: None, current, on_error=lambda _e: self._finish_load(current))

    def _on_chunk(self, payload) -> None:
        current, items, total, done, pages = payload
        if not current():
            return
        self.rows.extend(items)
        self._append_rows(items)
        if done >= pages:
            self._finish_load(current)
        else:
            self._update_summary(f"loading {len(self.rows):,} of {total:,} (page {done} of {pages})…")

    def _finish_load(self, current) -> None:
        if not current():
            return
        self._loading = False
        if self._sort:  # rows arrived in Honcho's order; apply the chosen sort once, now that all are here
            self._fill_table()
        else:
            self._update_summary()

    def _passes_filter(self, c: dict) -> bool:
        text = self.text_filter.text().strip().lower()
        if text and text not in (c.get("content") or "").lower():
            return False
        return not (self.unrated_only.isChecked() and self.ctx.store.rating(self.ws, c["id"]))

    def _visible_rows(self) -> list[dict]:
        rows = [c for c in self.rows if self._passes_filter(c)]
        if self._sort and not self._loading:
            column, order = self._sort
            rows.sort(key=lambda c: self._sort_value(c, column), reverse=order == Qt.DescendingOrder)
        return rows

    def _sort_value(self, c: dict, column: int):
        level = c.get("level", "")
        return [self.ctx.store.rating(self.ws, c["id"]) or "",
                CONCLUSION_LEVELS.index(level) if level in CONCLUSION_LEVELS else len(CONCLUSION_LEVELS),
                (c.get("content") or "").lower(),
                f"{c.get('observer_id')} → {c.get('observed_id')}".lower(),
                c.get("session_id") or "",
                c.get("created_at") or ""][column]

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
            self._set_row(r, c)
        self.table.blockSignals(False)
        self._fit_columns()
        self._fit_rows()
        self._update_summary()

    def _append_rows(self, new: list[dict]) -> None:
        """Add newly arrived rows below the existing ones (filter applied, Honcho's order kept)."""
        rows = [c for c in new if self._passes_filter(c)]
        if not rows:
            return
        start = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.setRowCount(start + len(rows))
        for r, c in enumerate(rows, start=start):
            self._set_row(r, c)
            if self.table.wordWrap():
                self.table.resizeRowToContents(r)
        self.table.blockSignals(False)
        if start == 0:
            self._fit_columns()

    def _set_row(self, r: int, c: dict) -> None:
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

    # -- columns: resize, wrap, sort, move
    def _fit_columns(self, measure: bool = True) -> None:
        """Small columns fit their contents and the Conclusion column gets the rest, until you drag an edge."""
        if self._widths_set_by_hand:
            return
        others = [c for c in range(len(COLUMNS)) if c != CONCLUSION_COL]
        self._fitting = True
        try:
            if measure:
                for col in others:
                    self.table.resizeColumnToContents(col)
            spare = self.table.viewport().width() - sum(self.table.columnWidth(c) for c in others)
            self.table.setColumnWidth(CONCLUSION_COL, max(MIN_CONCLUSION_WIDTH, spare))
        finally:
            self._fitting = False

    def _fit_rows(self) -> None:
        self._rows_timer.stop()
        self.table.resizeRowsToContents()  # wrapped: tall enough for the whole text; unwrapped: one line

    def _column_resized(self, _column: int, _old: int, _new: int) -> None:
        if not self._fitting:
            self._widths_set_by_hand = True
        if self.table.wordWrap():
            self._rows_timer.start()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.table.viewport() and event.type() == QEvent.Resize:
            self._fit_columns(measure=False)
        return super().eventFilter(obj, event)

    def fit_to_window(self) -> None:
        self._widths_set_by_hand = False
        self._fit_columns()

    def _wrap_toggled(self, on: bool) -> None:
        self.ctx.store.set_ui_value("conclusions_wrap", on)
        self.table.setWordWrap(on)
        self._fit_rows()

    def _sort_clicked(self, column: int) -> None:
        order = Qt.DescendingOrder if self._sort == (column, Qt.AscendingOrder) else Qt.AscendingOrder
        self._sort = (column, order)
        header = self.table.horizontalHeader()
        header.setSortIndicatorShown(True)
        header.setSortIndicator(column, order)
        if not self._loading:  # while loading, rows arrive in Honcho's order; the sort is applied at the end
            self._fill_table()

    def _save_order(self, *_args) -> None:
        header = self.table.horizontalHeader()
        self.ctx.store.set_ui_value("conclusions_columns", [header.logicalIndex(v) for v in range(header.count())])

    def _restore_order(self) -> None:
        order = self.ctx.store.ui_value("conclusions_columns")
        if not isinstance(order, list) or sorted(order) != list(range(len(COLUMNS))):
            return  # nothing saved, or the columns changed since: keep the default order
        header = self.table.horizontalHeader()
        for position, logical in enumerate(order):
            header.moveSection(header.visualIndex(logical), position)

    def reset_order(self) -> None:
        header = self.table.horizontalHeader()
        for logical in range(len(COLUMNS)):
            header.moveSection(header.visualIndex(logical), logical)
        self._save_order()

    def _header_menu(self, pos) -> None:
        menu = QMenu(self)
        fit = menu.addAction("Fit columns to the window")
        reset = menu.addAction("Reset column order")
        chosen = menu.exec(self.table.horizontalHeader().mapToGlobal(pos))
        if chosen is fit:
            self.fit_to_window()
        elif chosen is reset:
            self.reset_order()

    def _update_summary(self, progress: str = "") -> None:
        n = len(self.rows)
        from_messages = sum(1 for c in self.rows if c.get("level") == "explicit")
        acc = self.ctx.store.accuracy(self.ws, ids={c["id"] for c in self.rows})
        pct = f"{acc.percent}%" if acc.percent is not None else "–"
        self.summary_label.setText(
            f"{n:,} conclusions: {from_messages:,} from messages, {n - from_messages:,} from dreaming  ·  "
            f"rated {acc.rated} (✓{acc.correct} ×{acc.wrong} ?{acc.unsure})  ·  accuracy {pct}"
            + (f"  ·  {progress}" if progress else ""))

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
SEARCH_LIMIT = 100  # the most Honcho's search returns


class MessagesTab(_PeerTab):
    sessions_loaded = Signal(list)

    def __init__(self, ctx: AppContext):
        super().__init__(ctx)
        self._session: str | None = None
        self._messages: list[dict] = []
        self._page = 0
        self._pages = 1
        self._sessions: list[dict] = []
        self.sort_combo = tip(QComboBox(), explain.SESSION_SORT)
        for label, key in (("Newest first", "newest"), ("Oldest first", "oldest"), ("Last activity", "activity")):
            self.sort_combo.addItem(label, key)
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(ctx.store.ui_value("sessions_sort", "newest"))))
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        self.session_list = tip(QListWidget(), "Sessions (conversations) this peer took part in.")
        self.session_list.currentItemChanged.connect(self._open_session)
        copyable(self.session_list)
        self.session_filter = tip(QLineEdit(), explain.SESSION_FILTER)
        self.session_filter.setPlaceholderText("Filter sessions")
        self.session_filter.setClearButtonEnabled(True)
        self.session_filter.textChanged.connect(self._filter_sessions)
        self.search_box = tip(QLineEdit(), explain.MESSAGE_SEARCH)
        self.search_box.setPlaceholderText("Search this peer's messages, press Enter")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.returnPressed.connect(self._search)
        self.search_box.textChanged.connect(self._search_cleared)
        self.view = HtmlView()
        self.count_label = QLabel()
        self.more_btn = QPushButton("Load 50 more")
        self.more_btn.setEnabled(False)
        self.more_btn.clicked.connect(self._fetch)
        self.all_btn = tip(QPushButton("Load all"), "Fetch the rest of this conversation in one go.")
        self.all_btn.setEnabled(False)
        self.all_btn.clicked.connect(self._fetch_all)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.search_box)
        right_layout.addWidget(self.view)
        row = QHBoxLayout()
        row.addWidget(self.count_label)
        row.addStretch()
        row.addWidget(self.more_btn)
        row.addWidget(self.all_btn)
        right_layout.addLayout(row)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Sort:"))
        sort_row.addWidget(self.sort_combo, 1)
        left_layout.addLayout(sort_row)
        left_layout.addWidget(self.session_filter)
        left_layout.addWidget(self.session_list)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
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
        self.search_box.clear()  # after the list: clearing a search reopens the current session, and there is none
        self.view.setHtml(render.placeholder("Loading sessions…"))
        self.ctx.call(lambda c: c.peer_sessions(ws, peer), self._show_sessions, self.latest.ticket("sessions"))

    def _show_sessions(self, sessions: list[dict]) -> None:
        self._sessions = sessions
        self.sessions_loaded.emit([s["id"] for s in sessions])
        if not sessions:
            self.view.setHtml(render.placeholder("This peer has no sessions."))
        self._list_sessions(select_first=True)

    def _sort_changed(self) -> None:
        self.ctx.store.set_ui_value("sessions_sort", self.sort_combo.currentData())
        self._list_sessions()

    def _list_sessions(self, select_first: bool = False) -> None:
        """Fill the list in the chosen order. 'Last activity' needs the time of each session's newest message,
        which Honcho does not keep on the session: it is read from the local cache (local/session-activity.json)
        and fetched in the background for sessions not cached yet, then the list is re-sorted."""
        ws, mode = self.ws, self.sort_combo.currentData()
        store = self.ctx.store
        selected = self._session if not select_first else None
        sessions = list(self._sessions)
        if mode == "activity":
            sessions.sort(key=lambda s: store.session_activity(ws, s["id"]) or s.get("created_at") or "", reverse=True)
        else:
            sessions.sort(key=lambda s: s.get("created_at") or "", reverse=mode == "newest")
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for s in sessions:
            last = store.session_activity(ws, s["id"])
            shown = last if mode == "activity" and last else s.get("created_at")
            item = QListWidgetItem(f"{s['id']}   {render.short_time(shown)}")
            item.setData(Qt.UserRole, s["id"])
            item.setToolTip(json.dumps({"started": s.get("created_at"), "last activity": last or "(not fetched yet)",
                                        **{k: s.get(k) for k in ("metadata", "configuration", "is_active")}},
                                       indent=2, ensure_ascii=False))
            self.session_list.addItem(item)
            if s["id"] == selected:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        self._filter_sessions(self.session_filter.text())
        if select_first and sessions:
            self.session_list.setCurrentRow(0)
        if mode == "activity":
            missing = [s["id"] for s in sessions if not store.session_activity(ws, s["id"])]
            if missing:
                self.ctx.status.emit(f"Fetching the last activity of {len(missing)} sessions…")
                self.ctx.call(lambda c: c.sessions_last_activity(ws, missing), self._activity_fetched,
                              self.latest.ticket("activity"))

    def _activity_fetched(self, times: dict[str, str]) -> None:
        self.ctx.store.set_session_activity(self.ws, times)
        self.ctx.status.emit(f"Last activity known for {len(times)} more sessions.")
        if self.sort_combo.currentData() == "activity":
            self._list_sessions()

    def _open_session(self, current, _previous=None) -> None:
        if current is None:
            return
        self._session, self._messages, self._page, self._pages = current.data(Qt.UserRole), [], 0, 1
        self._fetch()

    def _fetch(self) -> None:
        ws, sid, page = self.ws, self._session, self._page + 1
        self.more_btn.setEnabled(False)
        self.all_btn.setEnabled(False)
        self.ctx.call(lambda c: c.messages_page(ws, sid, page), self._add_page, self.latest.ticket("messages"))

    def _fetch_all(self) -> None:
        ws, sid, after, pages = self.ws, self._session, self._page, self._pages
        self.more_btn.setEnabled(False)
        self.all_btn.setEnabled(False)
        self.count_label.setText(f"Loading all {pages} pages…")
        self.ctx.call(lambda c: c.messages_rest(ws, sid, after, pages),
                      lambda items: self._add_page({"items": items, "page": pages, "pages": pages}),
                      self.latest.ticket("messages"))

    def _add_page(self, data: dict) -> None:
        self._page = data.get("page", self._page + 1)
        self._pages = data.get("pages") or self._pages
        self._messages += data.get("items", [])
        self.view.setHtml(render.messages_html(self._messages))
        total = data.get("total") or (len(self._messages) if self._page >= self._pages else "?")
        self.count_label.setText(f"{len(self._messages)} of {total} messages")
        more = self._page < self._pages
        self.more_btn.setEnabled(more)
        self.all_btn.setEnabled(more)

    def _search(self) -> None:
        query = self.search_box.text().strip()
        if not query or not self.ws:
            return
        ws, peer = self.ws, self.peer
        self.more_btn.setEnabled(False)
        self.all_btn.setEnabled(False)
        self.count_label.setText("Searching…")
        self.ctx.call(lambda c: c.search_messages(ws, query, peer_id=peer, limit=SEARCH_LIMIT),
                      self._show_search, self.latest.ticket("messages"))

    def _show_search(self, rows: list[dict]) -> None:
        self.view.setHtml(render.messages_html(rows) if rows else render.placeholder("No messages found."))
        self.count_label.setText(f"{len(rows)} found" + (f", the {SEARCH_LIMIT} closest" if len(rows) >= SEARCH_LIMIT else ""))

    def _filter_sessions(self, text: str) -> None:
        """Hide sessions whose name and details (the tooltip: metadata, configuration) lack every typed word."""
        words = text.lower().split()
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            haystack = f"{item.text()} {item.toolTip()}".lower()
            item.setHidden(not all(w in haystack for w in words))

    def _search_cleared(self, text: str) -> None:
        if not text and self.session_list.currentItem() is not None:
            self._open_session(self.session_list.currentItem())


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
