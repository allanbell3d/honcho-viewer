"""Monitor tab: every workspace that is doing something, live. Rows appear by themselves as events arrive.

Fed by the telemetry listener (what Honcho is doing) and, optionally, by polling each workspace's queue status
(how much work is waiting), so a stuck run shows up even when it has gone quiet. Read-only: it only listens and reads.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import explain, render
from .monitor import NO_WORKSPACE, MonitorState
from .telemetry_panel import TelemetryPanel
from .widgets import AppContext, HtmlView, hint, tip

COLUMNS = (
    ("Workspace", "A workspace that has sent events, or one you chose to watch."),
    ("State", "active: events in the last 15 s. stalled: work is waiting in the queue but nothing was heard for "
              "longer than the 'stalled after' limit. done: the queue is empty. idle: quiet, nothing waiting."),
    ("Last event", "How long ago something was heard, and what it was doing."),
    ("Queue", "Honcho's own work queue for this workspace: done / total, and how much is running or waiting. "
              "Polled every few seconds."),
    ("Extraction", "Batches of messages turned into conclusions: batches, messages read, conclusions made."),
    ("Dreaming", "Dream runs, and the conclusions the specialists created (+) and deleted (-)."),
    ("Questions", "Questions answered (dialectic calls)."),
    ("LLM calls", "Model calls made, and how many in the last minute."),
    ("Tokens in / out", "Tokens sent to and produced by the models. Honcho can sample high-volume events; if it "
                        "does, these undercount."),
    ("Problems", "Failed model calls, retries and fallbacks to another model, plus failed extraction observers and "
                 "dream specialists."),
    ("Model(s)", "Models seen in the calls, most used first."),
)
STATE_STYLE = {"active": ("● active", "#1a8a3a"), "stalled": ("● STALLED", "#c62828"), "idle": ("○ idle", "#777777"),
               "done": ("✓ done", "#1565c0")}
STALLED_TINT = QColor(198, 40, 40, 38)
FILTERS = (
    ("Working now", "working"),
    ("Active (heard in last 15 s)", "active"),
    ("Needs attention", "attention"),
    ("Finished", "done"),
    ("Everything", "all"),
)
FILTER_TIPS = ("Working now: active, stalled, or with work waiting or running in its queue, or heard from in the "
               "last 10 minutes. Needs attention: stalled, or any failed call, retry, fallback or failed observer. "
               "Finished: queue empty and quiet.")
RECENT_S = 600
STATE_ORDER = {"stalled": 0, "active": 1, "idle": 2, "done": 3}  # default sort: trouble first
SORT_KEY = Qt.UserRole + 1


class _Item(QTableWidgetItem):
    """A cell that sorts by a value (number, tuple) stored with it instead of by its text."""

    def __lt__(self, other) -> bool:
        mine, theirs = self.data(SORT_KEY), other.data(SORT_KEY)
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                pass
        return self.text().lower() < other.text().lower()


def compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def queue_text(row: dict) -> str:
    total, done = row["queue_total"], row["queue_done"]
    if row["queue"] is None:
        return "–"
    if not total:
        return "empty"
    return (f"{done}/{total} ({round(100 * done / total)}%)"
            f" · {row['queue_running']} run · {row['queue_waiting']} wait")


class MonitorView(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.monitor: MonitorState = ctx.telemetry.monitor
        self._pending_refresh = False
        self._workspaces: list[str] = []
        self._shown: tuple = (None, "", "")  # (workspace, detail html, feed text) currently on screen

        self.totals_label = QLabel()
        self.totals_label.setTextFormat(Qt.RichText)
        self.clear_btn = tip(QPushButton("Clear"), "Forget everything shown here (the log on disk is not touched).")
        self.clear_btn.clicked.connect(self._clear)
        self.poll_check = tip(QCheckBox("Poll queue status"),
                              "Every 10 seconds, read each listed workspace's queue (a cheap, read-only call). "
                              "This is what shows progress, and what lets a silent workspace be flagged as stalled.")
        self.poll_check.setChecked(True)
        self.stall_spin = tip(QSpinBox(), "A workspace with work still waiting that has been silent for longer than "
                                          "this many minutes is flagged STALLED.")
        self.stall_spin.setRange(1, 240)
        self.stall_spin.setSuffix(" min")
        self.stall_spin.setValue(max(1, round(self.monitor.stall_after / 60)))
        self.stall_spin.valueChanged.connect(self._stall_changed)
        self.watch_combo = tip(QComboBox(), "Add a workspace to the table even before it has sent anything.")
        self.watch_btn = QPushButton("Watch")
        self.watch_btn.clicked.connect(self._watch_selected)
        self.watch_all_btn = tip(QPushButton("Watch all"), "Add every workspace on the server to the table.")
        self.watch_all_btn.clicked.connect(self._watch_all)

        self.filter_combo = tip(QComboBox(), FILTER_TIPS)
        for label, mode in FILTERS:
            self.filter_combo.addItem(label, mode)
        saved = self.ctx.store.ui_value("monitor_filter", "working")
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(saved)))
        self.filter_combo.currentIndexChanged.connect(self._filter_changed)
        self.text_filter = tip(QLineEdit(placeholderText="name contains… (comma = or)"),
                               "Show only workspaces whose name contains this text. Separate several with commas, "
                               "for example: test, prod")
        self.text_filter.setClearButtonEnabled(True)
        self.text_filter.textChanged.connect(lambda _text: self.refresh())
        self.only_selected = tip(QPushButton("Only selected"),
                                 "Show only the rows selected right now (Ctrl or Shift click to select several). "
                                 "Click again to show the others again.")
        self.only_selected.setCheckable(True)
        self.only_selected.toggled.connect(self._pin_toggled)
        self.count_label = QLabel()
        self._pinned: set[str] = set()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([name for name, _ in COLUMNS])
        for i, (_, why) in enumerate(COLUMNS):
            self.table.horizontalHeaderItem(i).setToolTip(why)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        header.setSortIndicator(1, Qt.AscendingOrder)  # State: stalled first, then active, idle, done
        header.setSectionsMovable(True)  # drag a column header to rearrange the table
        self._restore_order()
        header.sectionMoved.connect(self._save_order)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._update_detail)
        self.detail = HtmlView()
        self.feed = QPlainTextEdit()
        self.feed.setReadOnly(True)
        self.feed.setMaximumBlockCount(300)
        self.feed.setStyleSheet("font-family: Consolas, monospace;")
        self.feed.setPlaceholderText("Select a workspace to see its live events.")

        self.panel = TelemetryPanel(ctx)
        left = QWidget()
        left.setMinimumWidth(230)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(hint(explain.TAB_HINTS["monitor"]))
        left_layout.addWidget(QLabel("<b>Telemetry listener</b>"))
        left_layout.addWidget(self.panel)
        left_layout.addStretch(1)

        controls = QHBoxLayout()
        controls.addWidget(self.poll_check)
        controls.addWidget(QLabel("Stalled after"))
        controls.addWidget(self.stall_spin)
        controls.addStretch(1)
        controls.addWidget(self.watch_combo)
        controls.addWidget(self.watch_btn)
        controls.addWidget(self.watch_all_btn)
        controls.addWidget(self.clear_btn)
        detail_split = QSplitter(Qt.Horizontal)
        detail_split.addWidget(self.detail)
        detail_split.addWidget(self.feed)
        detail_split.setSizes([480, 640])
        main = QSplitter(Qt.Vertical)
        main.addWidget(self.table)
        main.addWidget(detail_split)
        main.setSizes([420, 260])
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.totals_label)
        right_layout.addLayout(controls)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Show"))
        filters.addWidget(self.filter_combo)
        filters.addWidget(self.text_filter, 1)
        filters.addWidget(self.only_selected)
        filters.addWidget(self.count_label)
        right_layout.addLayout(filters)
        right_layout.addWidget(main, 1)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([250, 1050])
        QVBoxLayout(self).addWidget(split)

        ctx.telemetry.events_received.connect(self._events_arrived)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.refresh)
        self._tick.start(1000)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.poll_now)
        self._poll_timer.start(10000)
        self.refresh()

    # ---- inputs
    def set_workspaces(self, ids: list[str]) -> None:
        self._workspaces = list(ids)
        self.watch_combo.clear()
        for ws in ids:
            self.watch_combo.addItem(self.ctx.ws_title(ws), ws)

    def _watch_selected(self) -> None:
        ws = self.watch_combo.currentData()
        if ws:
            self.monitor.watch(ws)
            self.refresh()
            self._poll([ws])

    def _watch_all(self) -> None:
        for ws in self._workspaces:
            self.monitor.watch(ws)
        self.refresh()
        self.poll_now()

    def _clear(self) -> None:
        self.monitor.clear()
        self.refresh()

    # ---- column order (drag headers; remembered between runs)
    def _save_order(self, *_args) -> None:
        header = self.table.horizontalHeader()
        self.ctx.store.set_ui_value("monitor_columns", [header.logicalIndex(v) for v in range(header.count())])

    def _restore_order(self) -> None:
        order = self.ctx.store.ui_value("monitor_columns")
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
        reset = menu.addAction("Reset column order")
        if menu.exec(self.table.horizontalHeader().mapToGlobal(pos)) is reset:
            self.reset_order()

    def _filter_changed(self, _index: int = 0) -> None:
        self.ctx.store.set_ui_value("monitor_filter", self.filter_combo.currentData())
        self.refresh()

    def _pin_toggled(self, on: bool) -> None:
        self._pinned = set(self.selected_workspaces()) if on else set()
        if on and not self._pinned:
            self.only_selected.setChecked(False)  # nothing selected: nothing to pin
            return
        self.refresh()

    def _visible(self, ws: str, r: dict) -> bool:
        if self._pinned:
            return ws in self._pinned
        patterns = [p.strip().lower() for p in self.text_filter.text().split(",") if p.strip()]
        if patterns and not any(p in ws.lower() for p in patterns):
            return False
        mode = self.filter_combo.currentData()
        problems = (r["errors"] + r["retries"] + r["fallbacks"] + r["extract_failed_observers"]
                    + r["specialists_failed"])
        if mode == "working":
            return (r["state"] in ("active", "stalled") or bool(r["queue_running"] or r["queue_waiting"])
                    or (r["age"] is not None and r["age"] < RECENT_S))
        if mode == "active":
            return r["state"] == "active"
        if mode == "attention":
            return r["state"] == "stalled" or problems > 0
        if mode == "done":
            return r["state"] == "done"
        return True

    def _stall_changed(self, minutes: int) -> None:
        self.monitor.stall_after = minutes * 60
        self.ctx.store.set_ui_value("monitor_stall_min", minutes)
        self.refresh()

    def _events_arrived(self, _count: int = 0) -> None:
        if not self._pending_refresh:  # many posts per second: redraw at most a few times a second
            self._pending_refresh = True
            QTimer.singleShot(250, self.refresh)

    # ---- queue polling
    def poll_now(self) -> None:
        if self.poll_check.isChecked():
            self._poll([ws for ws in self.monitor.workspaces() if ws != NO_WORKSPACE])

    def _poll(self, names: list[str]) -> None:
        if self.ctx.client is None or not names:
            return
        current = self.latest_ticket()
        for ws in names:
            self.ctx.call(lambda c, ws=ws: c.queue_status(ws), lambda status, ws=ws: self._queue_in(ws, status),
                          current, on_error=lambda exc, ws=ws: self.monitor.set_queue(ws, None))

    def latest_ticket(self):
        self._poll_serial = getattr(self, "_poll_serial", 0) + 1
        serial = self._poll_serial
        return lambda: serial == self._poll_serial

    def _queue_in(self, ws: str, status: dict) -> None:
        self.monitor.set_queue(ws, status)
        self.refresh()

    # ---- drawing
    def _cells(self, ws: str, r: dict) -> list[str]:
        extraction = (f"{r['extract_batches']} batches · {compact(r['extract_messages'])} msgs → "
                      f"{compact(r['extract_conclusions'])}" if r["extract_batches"] else "–")
        dreaming = (f"{r['dream_runs']} runs · +{compact(r['dream_created'])} −{compact(r['dream_deleted'])}"
                    if r["dream_runs"] or r["specialists"] else "–")
        problems = []
        if r["errors"]:
            problems.append(f"{r['errors']} failed")
        if r["retries"]:
            problems.append(f"{r['retries']} retried")
        if r["fallbacks"]:
            problems.append(f"{r['fallbacks']} fallback")
        if r["extract_failed_observers"]:
            problems.append(f"{r['extract_failed_observers']} observer fail")
        if r["specialists_failed"]:
            problems.append(f"{r['specialists_failed']} specialist fail")
        models = ", ".join(r["models"][:2]) + (f" +{len(r['models']) - 2}" if len(r["models"]) > 2 else "")
        last = ago(r["age"]) + (f" · {r['last_kind']}" if r["last_kind"] and r["age"] is not None else "")
        return [self.ctx.ws_title(ws) if ws != NO_WORKSPACE else ws, STATE_STYLE[r["state"]][0], last,
                queue_text(r), extraction, dreaming, str(r["questions"]) if r["questions"] else "–",
                f"{r['calls']} · {r['calls_per_min']}/min" if r["calls"] else "–",
                f"{compact(r['tokens_in'])} / {compact(r['tokens_out'])}" if r["calls"] else "–",
                " · ".join(problems) or "–", models]

    @staticmethod
    def _sort_keys(ws: str, r: dict) -> list:
        pct = r["queue_done"] / r["queue_total"] if r["queue_total"] else -1
        return [ws.lower(), (STATE_ORDER[r["state"]], ws.lower()), 1e12 if r["age"] is None else r["age"], pct,
                r["extract_batches"],
                r["dream_runs"] + r["specialists"], r["questions"], r["calls"], r["tokens_in"],
                r["errors"] + r["retries"] + r["fallbacks"] + r["extract_failed_observers"] + r["specialists_failed"],
                (r["models"] or [""])[0]]

    def refresh(self) -> None:
        self._pending_refresh = False
        snap = self.monitor.snapshot()
        self._update_totals()
        table = self.table
        table.setSortingEnabled(False)  # rows must not jump around while their cells are being updated
        existing = {table.item(r, 0).data(Qt.UserRole): r for r in range(table.rowCount())}
        for r in reversed(range(table.rowCount())):
            if table.item(r, 0).data(Qt.UserRole) not in snap:
                table.removeRow(r)
        existing = {table.item(r, 0).data(Qt.UserRole): r for r in range(table.rowCount())}
        new_rows = []
        for ws, row in snap.items():
            r = existing.get(ws)
            if r is None:
                r = table.rowCount()
                table.insertRow(r)
                for c in range(len(COLUMNS)):
                    table.setItem(r, c, _Item())
                table.item(r, 0).setData(Qt.UserRole, ws)
                new_rows.append(ws)
            for c, text in enumerate(self._cells(ws, row)):
                item = table.item(r, c)
                if item.text() != text:
                    item.setText(text)
            for c, key in enumerate(self._sort_keys(ws, row)):
                table.item(r, c).setData(SORT_KEY, key)
            colour = QColor(STATE_STYLE[row["state"]][1])
            table.item(r, 1).setForeground(QBrush(colour))
            tint = QBrush(STALLED_TINT) if row["state"] == "stalled" else QBrush()
            for c in range(len(COLUMNS)):
                table.item(r, c).setBackground(tint)
            table.item(r, 9).setForeground(QBrush(QColor("#c62828")) if row["errors"] else QBrush())
        table.setSortingEnabled(True)
        shown = 0
        for r in range(table.rowCount()):
            name = table.item(r, 0).data(Qt.UserRole)
            visible = self._visible(name, snap[name])
            table.setRowHidden(r, not visible)
            shown += visible
        self.count_label.setText(f"Showing {shown} of {len(snap)}")
        if new_rows and self.poll_check.isChecked():
            self._poll(new_rows)
        self._update_detail()

    def _update_totals(self) -> None:
        t = self.monitor.totals()
        if not t["workspaces"]:
            self.totals_label.setText("<i>Nothing yet. Start the listener and point Honcho's telemetry at it, or "
                                      "watch a workspace.</i>")
            return
        stalled = f"<b style='color:#c62828'>{t['stalled']} stalled</b>" if t["stalled"] else "0 stalled"
        errors = f"<b style='color:#c62828'>{t['errors']} failed calls</b>" if t["errors"] else "0 failed calls"
        self.totals_label.setText(
            f"<b>{t['workspaces']}</b> workspaces: <span style='color:#1a8a3a'>{t['active']} active</span> · "
            f"{stalled} · {t['idle']} idle · {t['done']} done &nbsp;|&nbsp; {t['events_per_s']} events/s · "
            f"{t['calls_per_min']} calls/min &nbsp;|&nbsp; {compact(t['tokens_in'])} in / {compact(t['tokens_out'])} "
            f"out tokens &nbsp;|&nbsp; {errors}")

    def selected_workspaces(self) -> list[str]:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows if not self.table.isRowHidden(r)]

    def selected_workspace(self) -> str | None:
        names = self.selected_workspaces()
        return names[0] if names else None

    def _update_detail(self) -> None:
        names = [n for n in self.selected_workspaces() if n in self.monitor.workspaces()]
        if len(names) > 1:
            self._update_multi(names)
            return
        ws = names[0] if names else None
        if ws is None:
            self._show(None, render.placeholder("Select a workspace above (Ctrl or Shift click to select several)."),
                       "")
            return
        row = self.monitor.snapshot()[ws]
        purposes = self.monitor.purposes(ws)
        body = [f"<h3>{render.esc(ws)}</h3>",
                f"<p>{render.esc(STATE_STYLE[row['state']][0])} · {row['events']} events · "
                f"{row['messages_created']} messages created · queue {render.esc(queue_text(row))}</p>"]
        if purposes:
            body.append("<p><b>Model calls by purpose</b></p><table cellpadding='3'><tr><th align='left'>purpose</th>"
                        "<th>calls</th><th>in</th><th>out</th><th>failed</th></tr>")
            for name, b in sorted(purposes.items(), key=lambda kv: -kv[1]["calls"]):
                body.append(f"<tr><td>{render.esc(name)}</td><td align='right'>{b['calls']}</td>"
                            f"<td align='right'>{compact(b['in'])}</td><td align='right'>{compact(b['out'])}</td>"
                            f"<td align='right'>{b['errors'] or ''}</td></tr>")
            body.append("</table>")
        errors = self.monitor.errors(ws)
        if errors:
            body.append(f"<p><b>Recent problems</b> ({len(errors)})</p><ul>")
            for e in errors[:15]:
                body.append(f"<li>{MonitorState.clock_text(e['at'])} {render.esc(e['what'])} "
                            f"{render.esc(e['model'])}: <b>{render.esc(e['error'])}</b></li>")
            body.append("</ul>")
        lines = [f"{MonitorState.clock_text(at)}  {text}" for at, text in self.monitor.recent(ws, 300)]
        self._show(ws, "".join(body), "\n".join(lines))

    def _update_multi(self, names: list[str]) -> None:
        """Several rows selected: one line each, the sum, and a merged live feed tagged with the workspace."""
        snap = self.monitor.snapshot()
        body = [f"<h3>{len(names)} workspaces selected</h3><table cellpadding='3'><tr><th align='left'>workspace</th>"
                "<th align='left'>state</th><th align='left'>queue</th><th>calls</th><th>in</th><th>out</th>"
                "<th>failed</th></tr>"]
        totals = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "errors": 0}
        for name in names:
            r = snap[name]
            for key in totals:
                totals[key] += r[key]
            body.append(f"<tr><td>{render.esc(name)}</td><td>{render.esc(STATE_STYLE[r['state']][0])}</td>"
                        f"<td>{render.esc(queue_text(r))}</td><td align='right'>{r['calls']}</td>"
                        f"<td align='right'>{compact(r['tokens_in'])}</td><td align='right'>{compact(r['tokens_out'])}</td>"
                        f"<td align='right'>{r['errors'] or ''}</td></tr>")
        body.append(f"<tr><td><b>total</b></td><td></td><td></td><td align='right'><b>{totals['calls']}</b></td>"
                    f"<td align='right'><b>{compact(totals['tokens_in'])}</b></td>"
                    f"<td align='right'><b>{compact(totals['tokens_out'])}</b></td>"
                    f"<td align='right'><b>{totals['errors'] or ''}</b></td></tr></table>")
        merged = sorted(((at, name, text) for name in names for at, text in self.monitor.recent(name, 100)),
                        reverse=True)[:300]
        lines = [f"{MonitorState.clock_text(at)}  [{name}] {text}" for at, name, text in merged]
        self._show("\n".join(names), "".join(body), "\n".join(lines))

    def _show(self, ws: str | None, html: str, feed: str) -> None:
        """Redraw the detail pane only when something changed, and keep the reader's scroll position."""
        shown_ws, shown_html, shown_feed = self._shown
        if html != shown_html:
            bar = self.detail.verticalScrollBar()
            keep = bar.value() if ws == shown_ws else 0
            self.detail.setHtml(html)
            bar.setValue(keep)
        if feed != shown_feed:
            bar = self.feed.verticalScrollBar()
            keep = bar.value() if ws == shown_ws else 0
            self.feed.setPlainText(feed)
            bar.setValue(keep)
        self._shown = (ws, html, feed)
