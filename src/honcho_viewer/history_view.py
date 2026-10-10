"""History tab: everything that happened in one workspace, job by job, from telemetry logs and live events.

A reader only: it shows the events the viewer received and any log files you open. Jobs are rebuilt by
``history.HistoryState``; this module draws them. The one server call is read-only (what a job wrote).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QMenu, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from . import explain, render
from .async_call import run_async
from .history import KIND_TITLES, HistoryState, Job
from .monitor_view import compact, queue_text
from .widgets import AppContext, HtmlView, hint, tip

COLUMNS = (
    ("Time", "When the job finished (your local time)."),
    ("Job", "extraction: messages turned into conclusions. dream: conclusions reworked by the dream specialists. "
            "question: a chat (dialectic) answer. summary: a session summary. The rest are single events."),
    ("Peer", "The peer the job was about (for extraction and dreams: the observed peer)."),
    ("Session", "The conversation (session) the job came from, when Honcho says."),
    ("Status", "ok · retried (ok after failed attempts) · partly failed (an observer or specialist failed) · "
               "failed (gave up) · incomplete (no end event in the log: still running, or not recorded)."),
    ("Duration", "From the first step to the end of the job."),
    ("Tokens in / out", "Tokens sent to and produced by the models for this job."),
    ("Wrote", "What the job changed: conclusions created (+) and deleted (−), peer card, messages."),
    ("Model(s)", "Models used by the job's calls."),
    ("Problems", "Failed model calls and retries, and the error of the last attempt."),
)
KIND_FILTERS = (
    ("Extraction", ("extraction",)),
    ("Dreams", ("dream",)),
    ("Questions", ("question",)),
    ("Summaries", ("summary",)),
    ("Messages and deletions", ("messages", "deletion")),
    ("Other", ("context", "maintenance", "call", "other")),
)
OTHER_TIP = "Context reads by your agents, maintenance (vector sync) and stray model calls. Off by default: noisy."
PERIODS = (("Last hour", 3600), ("Last 24 hours", 86400), ("Last 7 days", 7 * 86400), ("Last 30 days", 30 * 86400),
           ("All time", None), ("Custom…", "custom"))
STATUS_COLOURS = {"ok": "#1a8a3a", "retried": "#b26a00", "partly failed": "#c62828", "failed": "#c62828",
                  "incomplete": "#777777"}
FAILED_TINT = QColor(198, 40, 40, 38)
GAP_S = 30 * 60
MAX_ROWS = 3000
SORT_KEY = Qt.UserRole + 1


def clock(stamp: float) -> str:
    moment = datetime.fromtimestamp(stamp)
    today = datetime.now().date()
    return moment.strftime("%H:%M:%S") if moment.date() == today else moment.strftime("%d %b %H:%M:%S")


def span(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} s"
    if seconds < 3600:
        return f"{int(seconds // 60)} min {int(seconds % 60)} s"
    return f"{int(seconds // 3600)} h {int(seconds % 3600 // 60)} min"


def wrote_text(job: Job) -> str:
    parts = []
    created, deleted = sum(job.created.values()), sum(job.deleted.values())
    if job.kind == "deletion":
        return f"−{-job.messages} msg −{deleted} concl."
    if created:
        parts.append(f"+{created}")
    if deleted:
        parts.append(f"−{deleted}")
    if parts:
        parts[-1] += " concl."
    if job.card_updated:
        parts.append("card")
    if job.messages and job.kind == "messages":
        parts.append(f"+{job.messages} msg")
    return " ".join(parts) or "–"


def problems_text(job: Job) -> str:
    parts = []
    if job.errors:
        parts.append(f"{job.errors} failed call{'s' if job.errors != 1 else ''}")
    if job.final_error:
        parts.append(job.final_error)
    if job.fallbacks:
        parts.append(f"{job.fallbacks} fallback")
    if job.partly:
        parts.append("observer/specialist failed")
    return " · ".join(parts) or "–"


class _Item(QTableWidgetItem):
    def __lt__(self, other) -> bool:
        mine, theirs = self.data(SORT_KEY), other.data(SORT_KEY)
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                pass
        return self.text().lower() < other.text().lower()


class HistoryView(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.history: HistoryState = ctx.telemetry.history
        self._loaded_once = False
        self._loading = False
        self._pending_refresh = False
        self._shown_jobs: list[Job] = []
        self._detail_key: str | None = None
        self._wrote: dict[str, str] = {}  # job key -> html of what it wrote (fetched on request)
        self._queue: dict | None = None
        self._sort = (0, Qt.DescendingOrder)

        self.ws_combo = tip(QComboBox(), "The workspace whose history to show. Listed once it has any events.")
        self.ws_combo.setMinimumContentsLength(18)
        self.ws_combo.currentIndexChanged.connect(self._workspace_changed)
        self.period_combo = tip(QComboBox(), "Which time to show. Custom lets you pick both ends.")
        for label, value in PERIODS:
            self.period_combo.addItem(label, value)
        self.period_combo.setCurrentIndex(max(0, self.period_combo.findText(
            self.ctx.store.ui_value("history_period", "Last 24 hours"))))
        self.period_combo.currentIndexChanged.connect(self._period_changed)
        now = QDateTime.currentDateTime()
        self.from_edit = QDateTimeEdit(now.addDays(-1))
        self.to_edit = QDateTimeEdit(now)
        for edit in (self.from_edit, self.to_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd MMM yyyy HH:mm")
            edit.dateTimeChanged.connect(lambda _dt: self.refresh())
        self.open_btn = tip(QToolButton(), "Read other files or folders of Honcho telemetry events (.jsonl, "
                                           ".jsonl.gz). They are remembered and read again next time.")
        self.open_btn.setText("Open logs…")
        self.open_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.open_btn)
        menu.addAction("Folder…", self._open_folder)
        menu.addAction("Files…", self._open_files)
        menu.addSeparator()
        menu.addAction("Read new lines now", self.reload_files)
        menu.addAction("Forget opened logs", self._forget_sources)
        self.open_btn.setMenu(menu)
        self.live_check = tip(QCheckBox("Live"), "Update the table as events arrive from the listener, and re-read "
                                                 "opened logs every minute.")
        self.live_check.setChecked(True)

        self.kind_checks: list[tuple[QCheckBox, tuple]] = []
        saved_kinds = self.ctx.store.ui_value("history_kinds", None)
        for label, kinds in KIND_FILTERS:
            box = QCheckBox(label)
            box.setChecked(label in saved_kinds if isinstance(saved_kinds, list) else label != "Other")
            if label == "Other":
                box.setToolTip(OTHER_TIP)
            box.toggled.connect(self._kinds_changed)
            self.kind_checks.append((box, kinds))
        self.peer_combo = tip(QComboBox(), "Only jobs about this peer (as the observed peer, or as the observer).")
        self.session_combo = tip(QComboBox(), "Only jobs from this conversation.")
        self.session_combo.setMinimumContentsLength(24)
        for combo in (self.peer_combo, self.session_combo):
            combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self.failed_check = tip(QCheckBox("Problems only"), "Only failed and partly failed jobs.")
        self.failed_check.toggled.connect(lambda _on: self.refresh())

        self.pending_label = tip(QLabel(), "Honcho's work queue for this workspace right now (read-only, every "
                                           "10 s). Honcho reports only the counts: it does not list the waiting "
                                           "jobs themselves.")
        self.pending_label.setTextFormat(Qt.RichText)
        self.totals_label = QLabel()
        self.totals_label.setTextFormat(Qt.RichText)
        self.totals_label.setWordWrap(True)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        self.source_label.setStyleSheet("color: #666;")

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([name for name, _ in COLUMNS])
        for i, (_, why) in enumerate(COLUMNS):
            self.table.horizontalHeaderItem(i).setToolTip(why)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(*self._sort)
        header.sectionClicked.connect(self._sort_clicked)
        self.table.itemSelectionChanged.connect(self._update_detail)
        self.detail = HtmlView()
        self.wrote_btn = tip(QPushButton("Show what it wrote"),
                             "Fetch the conclusions this job created from Honcho (read-only): the observed peer's "
                             "conclusions created during the job. Ones deleted since cannot be shown.")
        self.wrote_btn.clicked.connect(self._fetch_wrote)
        self.wrote_btn.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(QLabel("Workspace"))
        top.addWidget(self.ws_combo)
        top.addWidget(self.period_combo)
        top.addWidget(self.from_edit)
        top.addWidget(QLabel("to"))
        top.addWidget(self.to_edit)
        top.addStretch(1)
        top.addWidget(self.live_check)
        top.addWidget(self.open_btn)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Show"))
        for box, _kinds in self.kind_checks:
            filters.addWidget(box)
        filters.addSpacing(12)
        filters.addWidget(QLabel("Peer"))
        filters.addWidget(self.peer_combo)
        filters.addWidget(QLabel("Session"))
        filters.addWidget(self.session_combo, 1)
        filters.addWidget(self.failed_check)
        detail_box = QWidget()
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(self.detail, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.wrote_btn)
        buttons.addStretch(1)
        detail_layout.addLayout(buttons)
        main = QSplitter(Qt.Vertical)
        main.addWidget(self.table)
        main.addWidget(detail_box)
        main.setSizes([430, 300])
        layout = QVBoxLayout(self)
        layout.addWidget(hint(explain.TAB_HINTS["history"]))
        layout.addLayout(top)
        layout.addLayout(filters)
        layout.addWidget(self.pending_label)
        layout.addWidget(self.totals_label)
        layout.addWidget(main, 1)
        layout.addWidget(self.source_label)
        self._period_changed(self.period_combo.currentIndex(), save=False)

        ctx.telemetry.events_received.connect(self._events_arrived)
        self._file_timer = QTimer(self)
        self._file_timer.timeout.connect(self._timed_reload)
        self._file_timer.start(60000)
        self._queue_timer = QTimer(self)
        self._queue_timer.timeout.connect(self.poll_queue)
        self._queue_timer.start(10000)

    # ---- loading
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.load_sources()
        else:
            self._live_refresh()  # events that arrived while the tab was hidden
        self.poll_queue()

    def sources(self) -> list[str]:
        extra = self.ctx.store.ui_value("history_sources", [])
        return [str(self.ctx.telemetry.log.folder)] + [p for p in extra if isinstance(p, str)]

    def load_sources(self, paths: list[str] | None = None, wait: bool = False) -> None:
        """Read the sources in the background (``wait`` reads them here, for tests)."""
        targets = paths or self.sources()
        if wait:
            self._loaded(self.history.load_paths(targets))
            return
        if self._loading:
            return
        self._loading = True
        self.source_label.setText("Reading logs…")
        run_async(lambda: self.history.load_paths(targets), self._loaded, self._load_failed)

    def reload_files(self) -> None:
        if not self._loading:
            self._loading = True
            run_async(self.history.refresh_files, self._loaded, self._load_failed)

    def _timed_reload(self) -> None:
        if self.live_check.isChecked() and self._loaded_once and self.isVisible():
            self.reload_files()

    def _loaded(self, counts: dict) -> None:
        self._loading = False
        bad = f" · {counts['bad']} unreadable lines skipped" if counts.get("bad") else ""
        self.source_label.setText(
            f"{self.history.events:,} events from {counts.get('files', 0)} file(s) and the listener{bad}. "
            f"Sources: " + "; ".join(self.history.sources))
        self._fill_workspaces()
        self.refresh()
        self.poll_queue()

    def _load_failed(self, exc: Exception) -> None:
        self._loading = False
        self.source_label.setText(f"Could not read the logs: {exc}")

    def _open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Folder of Honcho telemetry logs")
        if folder:
            self._add_sources([folder])

    def _open_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Honcho telemetry log files", "",
                                                "Telemetry logs (*.jsonl *.jsonl.gz *.json);;All files (*)")
        if files:
            self._add_sources(files)

    def _add_sources(self, paths: list[str]) -> None:
        extra = [p for p in self.ctx.store.ui_value("history_sources", []) if isinstance(p, str)]
        for p in paths:
            if p not in extra:
                extra.append(p)
        self.ctx.store.set_ui_value("history_sources", extra)
        self.load_sources(paths)

    def _forget_sources(self) -> None:
        self.ctx.store.set_ui_value("history_sources", [])
        self.source_label.setText("Opened logs forgotten. Restart the app to drop their events from the table.")

    # ---- filters
    def _fill_workspaces(self) -> None:
        names = self.history.workspaces()
        current = self.ws_combo.currentData() or self.ctx.store.ui_value("history_workspace", "")
        self.ws_combo.blockSignals(True)
        self.ws_combo.clear()
        for name in names:
            self.ws_combo.addItem(self.ctx.ws_title(name), name)
        index = self.ws_combo.findData(current)
        if index < 0 and names:  # nothing chosen yet: the busiest workspace
            busiest = max(names, key=lambda n: len(self.history.select(n)))
            index = self.ws_combo.findData(busiest)
        self.ws_combo.setCurrentIndex(max(0, index))
        self.ws_combo.blockSignals(False)
        self._fill_peer_filters()

    def _fill_peer_filters(self) -> None:
        ws = self.ws_combo.currentData()
        for combo, values, first in ((self.peer_combo, self.history.peers(ws) if ws else [], "all peers"),
                                     (self.session_combo, self.history.sessions(ws) if ws else [], "all sessions")):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(first, None)
            for value in values:
                combo.addItem(value, value)
            combo.setCurrentIndex(max(0, combo.findData(current)) if current else 0)
            combo.blockSignals(False)

    def _workspace_changed(self, _index: int = 0) -> None:
        ws = self.ws_combo.currentData()
        if ws:
            self.ctx.store.set_ui_value("history_workspace", ws)
        self._queue = None
        self._fill_peer_filters()
        self.refresh()
        self.poll_queue()

    def _period_changed(self, _index: int = 0, save: bool = True) -> None:
        custom = self.period_combo.currentData() == "custom"
        self.from_edit.setVisible(custom)
        self.to_edit.setVisible(custom)
        if save:
            self.ctx.store.set_ui_value("history_period", self.period_combo.currentText())
        self.refresh()

    def _kinds_changed(self, _on: bool = False) -> None:
        self.ctx.store.set_ui_value("history_kinds", [b.text() for b, _ in self.kind_checks if b.isChecked()])
        self.refresh()

    def _window(self) -> tuple[float | None, float | None]:
        value = self.period_combo.currentData()
        if value == "custom":
            return self.from_edit.dateTime().toSecsSinceEpoch(), self.to_edit.dateTime().toSecsSinceEpoch()
        return (time.time() - value if value else None), None

    def selected_kinds(self) -> set[str]:
        return {k for box, kinds in self.kind_checks if box.isChecked() for k in kinds}

    def current_jobs(self) -> list[Job]:
        ws = self.ws_combo.currentData()
        if not ws:
            return []
        start, end = self._window()
        return self.history.select(ws, self.selected_kinds(), start, end, self.peer_combo.currentData(),
                                   self.session_combo.currentData(), self.failed_check.isChecked())

    # ---- live
    def _events_arrived(self, _count: int = 0) -> None:
        if self.live_check.isChecked() and self.isVisible() and not self._pending_refresh:
            self._pending_refresh = True
            QTimer.singleShot(1000, self._live_refresh)

    def _live_refresh(self) -> None:
        self._pending_refresh = False
        if self.ws_combo.count() != len(self.history.workspaces()):
            self._fill_workspaces()
        self.refresh()

    def poll_queue(self) -> None:
        ws = self.ws_combo.currentData()
        if not ws or self.ctx.client is None or not self.isVisible():
            self._draw_pending()
            return
        self.ctx.call(lambda c: c.queue_status(ws), lambda status: self._queue_in(ws, status),
                      lambda: self.ws_combo.currentData() == ws, on_error=lambda _e: self._queue_in(ws, None))

    def _queue_in(self, ws: str, status: dict | None) -> None:
        if ws == self.ws_combo.currentData():
            self._queue = status
            self._draw_pending()

    def _draw_pending(self) -> None:
        q = self._queue
        if q is None:
            self.pending_label.setText("<b>Pending now:</b> <i>unknown (not connected, or not read yet)</i>")
            return
        row = {"queue": q, "queue_total": q.get("total_work_units", 0) or 0,
               "queue_done": q.get("completed_work_units", 0) or 0,
               "queue_running": q.get("in_progress_work_units", 0) or 0,
               "queue_waiting": q.get("pending_work_units", 0) or 0}
        waiting = row["queue_waiting"]
        colour = "#b26a00" if waiting else "#1a8a3a"
        self.pending_label.setText(f"<b>Pending now:</b> <span style='color:{colour}'>{waiting} waiting</span> · "
                                   f"{row['queue_running']} running · queue {render.esc(queue_text(row))}")

    # ---- drawing
    def _sort_clicked(self, column: int) -> None:
        current, order = self._sort
        order = (Qt.AscendingOrder if order == Qt.DescendingOrder else Qt.DescendingOrder) if column == current \
            else (Qt.DescendingOrder if column in (0, 5, 6) else Qt.AscendingOrder)
        self._sort = (column, order)
        self.table.horizontalHeader().setSortIndicator(column, order)
        self.refresh()

    @staticmethod
    def _sort_value(job: Job, column: int):
        return [job.end, job.kind, job.peer.lower(), job.session.lower(), job.status, job.duration, job.tokens_in,
                sum(job.created.values()) + sum(job.deleted.values()), ",".join(job.models), job.errors][column]

    def _cells(self, job: Job) -> list[str]:
        tokens = f"{compact(job.tokens_in)} / {compact(job.tokens_out)}" if job.tokens_in or job.tokens_out else "–"
        peer = job.peer + (f" (by {job.observer})" if job.observer and job.observer != job.peer else "")
        return [clock(job.end), KIND_TITLES.get(job.kind, job.kind), peer or "–", job.session or "–", job.status,
                span(job.duration) if job.duration else "–", tokens, wrote_text(job),
                ", ".join(job.models) or "–", problems_text(job)]

    def refresh(self) -> None:
        jobs = self.current_jobs()
        self._draw_totals(jobs)
        column, order = self._sort
        jobs.sort(key=lambda j: self._sort_value(j, column), reverse=order == Qt.DescendingOrder)
        more = len(jobs) - MAX_ROWS
        jobs = jobs[:MAX_ROWS]
        self._shown_jobs = jobs
        keep = self._detail_key
        rows: list[Job | str] = []
        for job in jobs:  # sorted by time: mark long quiet stretches
            if column == 0 and rows and isinstance(rows[-1], Job):
                gap = abs(rows[-1].end - job.end) - (rows[-1].duration if order == Qt.DescendingOrder
                                                     else job.duration)
                if gap > GAP_S:
                    rows.append(f"─── {span(gap)} quiet ───")
            rows.append(job)
        table = self.table
        table.setUpdatesEnabled(False)
        table.clearSpans()
        table.setRowCount(len(rows))
        select_row = -1
        for r, entry in enumerate(rows):
            if isinstance(entry, str):
                item = _Item(entry)
                item.setFlags(Qt.ItemIsEnabled)
                item.setForeground(QBrush(QColor("#888888")))
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(r, 0, item)
                for c in range(1, len(COLUMNS)):
                    table.setItem(r, c, _Item(""))
                table.setSpan(r, 0, 1, len(COLUMNS))
                continue
            tint = QBrush(FAILED_TINT) if entry.problem else QBrush()
            for c, text in enumerate(self._cells(entry)):
                item = _Item(text)
                item.setBackground(tint)
                if c == 4:
                    item.setForeground(QBrush(QColor(STATUS_COLOURS.get(entry.status, "#000000"))))
                table.setItem(r, c, item)
            table.item(r, 0).setData(Qt.UserRole, entry.key)
            if entry.key == keep:
                select_row = r
        table.setUpdatesEnabled(True)
        if not self._sized and rows:
            self._sized = True
            table.resizeColumnsToContents()
            table.setColumnWidth(3, min(table.columnWidth(3), 260))
        if select_row >= 0:
            table.blockSignals(True)
            table.selectRow(select_row)
            table.blockSignals(False)
        if more > 0:
            self.totals_label.setText(self.totals_label.text() + f"<br><i>Showing the first {MAX_ROWS:,} rows of "
                                                                 f"{MAX_ROWS + more:,}: narrow the period or filters.</i>")
        self._update_detail()

    _sized = False

    def _draw_totals(self, jobs: list[Job]) -> None:
        if not self.ws_combo.count():
            self.totals_label.setText("<i>No events yet. Start the listener (Monitor or Compare reasoning tab), or "
                                      "open log files with <b>Open logs…</b>.</i>")
            return
        t = HistoryState.totals(jobs)
        kinds = " · ".join(f"{n:,} {KIND_TITLES.get(k, k)}" for k, n in
                           sorted(t["by_kind"].items(), key=lambda kv: -kv[1]))
        failed = (f"<b style='color:#c62828'>{t['failed']} failed</b>" if t["failed"] else "0 failed")
        partly = f" · <b style='color:#c62828'>{t['partly']} partly failed</b>" if t["partly"] else ""
        incomplete = f" · {t['incomplete']} incomplete" if t["incomplete"] else ""
        self.totals_label.setText(
            f"<b>{t['jobs']:,}</b> shown: {kinds or 'nothing'} &nbsp;|&nbsp; {failed}{partly}{incomplete} "
            f"&nbsp;|&nbsp; {compact(t['tokens_in'])} in / {compact(t['tokens_out'])} out tokens &nbsp;|&nbsp; "
            f"conclusions +{t['created']:,} −{t['deleted']:,}")

    def selected_job(self) -> Job | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        key = item.data(Qt.UserRole) if item else None
        return self.history.jobs.get(key) if key else None

    def _update_detail(self) -> None:
        job = self.selected_job()
        self._detail_key = job.key if job else None
        self.wrote_btn.setEnabled(bool(job and job.kind in ("extraction", "dream") and sum(job.created.values())
                                       and self.ctx.client is not None))
        if job is None:
            self.detail.setHtml(render.placeholder("Select a job to see its steps: iterations, model calls (with "
                                                   "errors and retries), tool calls and what it wrote."))
            return
        bar = self.detail.verticalScrollBar()
        keep = bar.value() if getattr(self, "_detail_shown", None) == job.key else 0
        self._detail_shown = job.key
        self.detail.setHtml(self.detail_html(job))
        bar.setValue(keep)

    def detail_html(self, job: Job) -> str:
        e = render.esc
        colour = STATUS_COLOURS.get(job.status, "#000")
        head = [f"<h3>{e(KIND_TITLES.get(job.kind, job.kind))} · <span style='color:{colour}'>{e(job.status)}</span>"
                f"</h3><p>{e(clock(job.start))} → {e(clock(job.end))} ({e(span(job.duration))})"]
        if job.peer:
            head.append(f" · peer <b>{e(job.peer)}</b>" + (f" observed by <b>{e(job.observer)}</b>"
                                                            if job.observer and job.observer != job.peer else ""))
        if job.session:
            head.append(f" · session {e(job.session)}")
        head.append("</p><p>")
        if job.calls or job.tokens_in:
            head.append(f"{job.calls} model call(s) · {job.tokens_in:,} in / {job.tokens_out:,} out tokens"
                        + (f" · {job.call_cached:,} cached" if job.call_cached else "")
                        + (f" · {job.embeddings} embedding call(s)" if job.embeddings else "")
                        + (f" · {', '.join(map(e, job.models))}" if job.models else ""))
        if job.created or job.deleted:
            made = ", ".join(f"+{n} {e(level)}" for level, n in job.created.items())
            gone = ", ".join(f"−{n} {e(level)}" for level, n in job.deleted.items())
            head.append(f"<br>Wrote: {made}{' · ' if made and gone else ''}{gone}"
                        + (" · peer card updated" if job.card_updated else ""))
        if job.errors:
            head.append(f"<br><b style='color:#c62828'>{job.errors} failed call(s)</b>"
                        + (f", last error: <b>{e(job.final_error)}</b>" if job.final_error else "")
                        + (f" · {job.retries} retries" if job.retries else ""))
        head.append("</p>")
        steps = ["<p><b>Steps</b></p><table cellpadding='3'>"]
        for step in sorted(job.steps, key=lambda s: s["t"]):
            colour = "#c62828" if not step["ok"] else "#000000"
            fields = ", ".join(f"{e(k)}={e(str(v))}" for k, v in step["fields"].items()
                               if k not in ("timestamp", "workspace_name", "run_id"))
            steps.append(f"<tr><td valign='top'>{e(datetime.fromtimestamp(step['t']).strftime('%H:%M:%S.%f')[:-3])}"
                         f"</td><td><span style='color:{colour}'>{e(step['text'])}</span>"
                         f"<br><small style='color:#777'>{fields}</small></td></tr>")
        steps.append("</table>")
        wrote = self._wrote.get(job.key, "")
        return "".join(head) + wrote + "".join(steps)

    # ---- what a job wrote (read-only fetch)
    def _fetch_wrote(self) -> None:
        job = self.selected_job()
        if job is None:
            return
        ws, key = job.workspace, job.key
        start = datetime.fromtimestamp(job.start - 1, timezone.utc)
        end = datetime.fromtimestamp(job.end + 2, timezone.utc)
        # a dream writes as one observer; extraction writes only explicit conclusions (for every observer)
        observer = job.observer if job.kind == "dream" else None
        level = "explicit" if job.kind == "extraction" else None
        overlapping = [o for o in self.history.select(ws, {job.kind}, job.start - 1, job.end + 2)
                       if o.key != key and o.peer == job.peer and (observer is None or o.observer == observer)]
        self._wrote[key] = "<p><i>Fetching what it wrote…</i></p>"
        self._update_detail()
        self.ctx.call(lambda c: c.conclusions_between(ws, start.isoformat(), end.isoformat(), job.peer or None,
                                                      observer, level),
                      lambda rows: self._wrote_in(key, rows, bool(overlapping)),
                      on_error=lambda exc: self._wrote_in(key, None, False, str(exc)))

    def _wrote_in(self, key: str, rows: list[dict] | None, mixed: bool, error: str = "") -> None:
        e = render.esc
        if rows is None:
            self._wrote[key] = f"<p style='color:#c62828'>Could not fetch: {e(error)}</p>"
        elif not rows:
            self._wrote[key] = ("<p><b>What it wrote:</b> <i>nothing found. The conclusions may have been deleted "
                                "since (for example by a later dream).</i></p>")
        else:
            note = (" <i>Another job for this peer ran at the same time, so some of these may be its.</i>"
                    if mixed else "")
            items = "".join(f"<li>[{e(r.get('level') or '?')}] {e(r.get('content') or '')} <small style='color:#777'>"
                            f"by {e(r.get('observer_id') or '?')}</small></li>" for r in rows)
            self._wrote[key] = f"<p><b>What it wrote ({len(rows)})</b>{note}</p><ul>{items}</ul>"
        if self._detail_key == key:
            self._update_detail()
