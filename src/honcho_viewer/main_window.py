"""Main window: connection bar, Explore tab (workspaces → peers → peer tabs) and the two Compare tabs."""
from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from . import __version__, render
from .async_call import Latest
from .client import HonchoClient
from .compare_peers_view import ComparePeersView
from .compare_reasoning_view import CompareReasoningView
from .compare_view import CompareView
from .history_view import HistoryView
from .monitor_view import MonitorView
from .peer_view import PeerView
from .scopes_view import ScopesView
from .store import LocalStore
from .widgets import AppContext, copyable, tip

PEER_SORTS = ("As listed by Honcho", "Name A-Z", "Name Z-A")
QUEUE_TIP = ("Honcho processes new messages in the background (conclusions, summaries, dreams). "
             "While work is waiting, what you see may be incomplete: wait for 'idle' before comparing models.")


class MainWindow(QMainWindow):
    def __init__(self, store: LocalStore):
        super().__init__()
        self.store = store
        self.ctx = AppContext(store)
        self.latest = Latest()
        self._workspaces: dict[str, dict] = {}
        self.setWindowTitle(f"Honcho Viewer {__version__}  (read-only)")

        # connection bar
        self.url_edit = tip(QLineEdit(store.base_url, placeholderText="http://your-server:8000"),
                            "Honcho server address, e.g. http://your-server:8000 (without /docs). "
                            "Remembered on this PC after you press Connect.")
        self.token_edit = tip(QLineEdit(store.token), "Bearer token (JWT). Saved on this PC in local/settings.json, "
                                                      "which is excluded from git.")
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.returnPressed.connect(self.connect_to_server)
        show_token = QCheckBox("show")
        show_token.toggled.connect(
            lambda on: self.token_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_to_server)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Server"))
        bar.addWidget(self.url_edit, 2)
        bar.addWidget(QLabel("Token"))
        bar.addWidget(self.token_edit, 3)
        bar.addWidget(show_token)
        bar.addWidget(self.connect_btn)

        # explore tab
        self.workspace_list = tip(QListWidget(), "A workspace is an isolated memory space (e.g. one app or one test run).")
        self.workspace_list.currentItemChanged.connect(self._on_workspace)
        self.label_edit = tip(QLineEdit(placeholderText="Model / label, e.g. qwen3-32b"),
                              "Your note for this workspace, e.g. the model that processed it. Stored on this PC only.")
        self.label_edit.setEnabled(False)
        self.label_edit.editingFinished.connect(self._save_label)
        self.ws_info = tip(QLabel(), QUEUE_TIP)
        self.ws_info.setWordWrap(True)
        self.peer_list = tip(QListWidget(), "A peer is any participant Honcho remembers: a user, an agent, …")
        self.peer_list.currentItemChanged.connect(self._on_peer)
        self.hide_off = tip(QCheckBox("Hide peers with reasoning off"),
                            "Hides peers whose configuration has observe_me off (Honcho writes no conclusions about them, "
                            "e.g. tenants). Their messages are still stored.")
        self.hide_off.setChecked(self.ctx.hide_reasoning_off)
        self.hide_off.toggled.connect(self.ctx.set_hide_reasoning_off)
        self.ctx.peer_filter_changed.connect(self._apply_hide_off)
        self.peer_filter = tip(QLineEdit(placeholderText="Filter peers…"),
                               "Shows only peers whose name contains this text.")
        self.peer_filter.setClearButtonEnabled(True)
        self.peer_filter.textChanged.connect(self._show_rows)
        self.peer_sort = tip(QComboBox(), "Order of the peer list.")
        self.peer_sort.addItems(PEER_SORTS)
        self.peer_sort.setCurrentIndex(int(store.ui_value("peer_sort", 0) or 0) % len(PEER_SORTS))
        self.peer_sort.currentIndexChanged.connect(lambda mode: (store.set_ui_value("peer_sort", mode),
                                                                 self._sort_peers()))
        self.show_hidden = tip(QCheckBox("Show hidden"),
                               "Shows the peers you hid (select peers, right-click → Hide). Saved on this PC only.")
        self.show_hidden.toggled.connect(self._show_rows)
        self.peer_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        copyable(self.peer_list)
        for text, hide in (("Hide selected peers", True), ("Unhide selected peers", False)):
            action = QAction(text, self.peer_list)
            action.triggered.connect(lambda _checked=False, hide=hide: self._hide_selected(hide))
            self.peer_list.addAction(action)
        self.peer_view = PeerView(self.ctx)

        left = QWidget()
        left.setMinimumWidth(230)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("<b>Workspaces</b>"))
        left_layout.addWidget(self.workspace_list, 2)
        left_layout.addWidget(self.label_edit)
        left_layout.addWidget(self.ws_info)
        left_layout.addWidget(QLabel("<b>Peers</b>"))
        left_layout.addWidget(self.hide_off)
        left_layout.addWidget(self.peer_filter)
        sort_row = QHBoxLayout()
        sort_row.addWidget(self.peer_sort, 1)
        sort_row.addWidget(self.show_hidden)
        left_layout.addLayout(sort_row)
        left_layout.addWidget(self.peer_list, 3)
        explore = QSplitter(Qt.Horizontal)
        explore.addWidget(left)
        explore.addWidget(self.peer_view)
        explore.setSizes([260, 1000])

        self.compare = CompareView(self.ctx)
        self.compare_peers = ComparePeersView(self.ctx)
        self.compare_reasoning = CompareReasoningView(self.ctx)
        self.monitor = MonitorView(self.ctx)
        self.history = HistoryView(self.ctx)
        self.scopes = ScopesView(self.ctx)
        self.tabs = QTabWidget()
        self.tabs.addTab(explore, "Explore")
        self.tabs.addTab(self.compare, "Compare models")
        self.tabs.addTab(self.compare_peers, "Compare peers")
        self.tabs.addTab(self.compare_reasoning, "Compare reasoning")
        self.tabs.addTab(self.monitor, "Monitor")
        self.tabs.addTab(self.history, "History")
        self.tabs.addTab(self.scopes, "Scopes")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(bar)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)
        self.ctx.status.connect(self.statusBar().showMessage)

        geometry = store.ui_value("geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        else:
            self.resize(1320, 840)
        if store.token and store.base_url:
            QTimer.singleShot(0, self.connect_to_server)
        else:
            self.statusBar().showMessage("Enter the server address and your Honcho token, then press Connect.")

    # ---- connection
    def connect_to_server(self) -> None:
        url, token = self.url_edit.text().strip(), self.token_edit.text().strip()
        if url.rstrip("/").endswith("/docs"):
            self.statusBar().showMessage("The server URL should not include /docs, e.g. http://your-server:8000")
            return
        if not url:
            self.statusBar().showMessage("Enter your Honcho server address (e.g. http://your-server:8000) first.")
            return
        self.store.set_connection(url, token)
        self.ctx.client = HonchoClient(url, token)
        self.ctx.peers_cache.clear()
        self.workspace_list.clear()
        self.peer_list.clear()
        self.peer_view.clear()
        self.statusBar().showMessage(f"Connecting to {url}…")
        self.ctx.call(lambda c: c.list_workspaces(), lambda rows: self._show_workspaces(url, rows),
                      self.latest.ticket("connect"))

    def _show_workspaces(self, url: str, rows: list[dict]) -> None:
        self._workspaces = {w["id"]: w for w in rows}
        self.workspace_list.blockSignals(True)
        self.workspace_list.clear()
        for w in rows:
            item = QListWidgetItem(self.ctx.ws_title(w["id"]))
            item.setData(Qt.UserRole, w["id"])
            item.setToolTip(json.dumps({k: w.get(k) for k in ("metadata", "configuration", "created_at")},
                                       indent=2, ensure_ascii=False))
            self.workspace_list.addItem(item)
        self.workspace_list.blockSignals(False)
        self.compare.set_workspaces(list(self._workspaces))
        self.compare_peers.set_workspaces(list(self._workspaces))
        self.compare_reasoning.set_workspaces(list(self._workspaces))
        self.monitor.set_workspaces(list(self._workspaces))
        self.scopes.set_workspaces(list(self._workspaces))
        self.statusBar().showMessage(f"Connected to {url} · {len(rows)} workspaces")

    # ---- selection
    def _on_workspace(self, current, _previous=None) -> None:
        if current is None:
            return
        ws = current.data(Qt.UserRole)
        self.label_edit.setEnabled(True)
        self.label_edit.setText(self.store.label(ws))
        self.peer_list.clear()
        self.peer_view.clear()
        self.ws_info.setText("Processing queue: …")
        ok = self.latest.ticket("workspace")
        self.ctx.call(lambda c: c.list_peers(ws), lambda peers: self._show_peers(ws, peers), ok)
        self.ctx.call(lambda c: c.queue_status(ws),
                      lambda q: self.ws_info.setText(f"Processing queue: {render.queue_text(q)}"), ok)

    def _show_peers(self, ws: str, peers: list[dict]) -> None:
        self.ctx.note_peers(ws, peers)
        self.peer_list.blockSignals(True)
        self.peer_list.clear()
        for p in peers:
            item = QListWidgetItem(p["id"])
            item.setData(Qt.UserRole, p["id"])
            item.setToolTip(json.dumps({k: p.get(k) for k in ("metadata", "configuration", "created_at")},
                                       indent=2, ensure_ascii=False))
            self.peer_list.addItem(item)
        self.peer_list.blockSignals(False)
        self._sort_peers()

    def _ws(self) -> str | None:
        item = self.workspace_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _hidden(self, ws: str | None) -> set[str]:
        """Peers hidden by hand in this workspace (saved on this PC)."""
        return set((self.store.ui_value("hidden_peers", {}) or {}).get(ws or "", []))

    def _hide_selected(self, hide: bool) -> None:
        ws = self._ws()
        if ws is None:
            return
        names = {i.data(Qt.UserRole) for i in self.peer_list.selectedItems() if not i.isHidden()}
        saved = dict(self.store.ui_value("hidden_peers", {}) or {})
        saved[ws] = sorted(self._hidden(ws) | names if hide else self._hidden(ws) - names)
        self.store.set_ui_value("hidden_peers", saved)
        self._show_rows()

    def _sort_peers(self) -> None:
        """Order the peer list (Honcho's order or by name) and keep the open peer selected."""
        mode = self.peer_sort.currentIndex()
        order = list(self.ctx.peers_cache.get(self._ws(), []))
        if mode:
            order.sort(key=str.lower, reverse=mode == 2)
        rank = {name: i for i, name in enumerate(order)}
        current = self.peer_list.currentItem()
        current_name = current.data(Qt.UserRole) if current else None
        self.peer_list.blockSignals(True)
        items = [self.peer_list.takeItem(0) for _ in range(self.peer_list.count())]
        for item in sorted(items, key=lambda i: rank.get(i.data(Qt.UserRole), len(rank))):
            self.peer_list.addItem(item)
            if item.data(Qt.UserRole) == current_name:
                self.peer_list.setCurrentItem(item)
        self.peer_list.blockSignals(False)
        self._show_rows()

    def _show_rows(self) -> None:
        """Show or hide list rows (reasoning off, name filter, hidden by hand); close the open peer if it is hidden.
        Touches the list only: nothing is fetched."""
        ws = self._ws()
        shown = set(self.ctx.visible_peers(ws, self.ctx.peers_cache.get(ws, []))) if ws else set()
        text = self.peer_filter.text().strip().lower()
        by_hand = set() if self.show_hidden.isChecked() else self._hidden(ws)
        for i in range(self.peer_list.count()):
            item = self.peer_list.item(i)
            name = item.data(Qt.UserRole)
            item.setHidden(name not in shown or text not in name.lower() or name in by_hand)
        hidden = self.peer_list.count() - len(shown)
        self.hide_off.setText("Hide peers with reasoning off" + (f" ({hidden} hidden)" if hidden > 0 else ""))
        current = self.peer_list.currentItem()
        if current is not None and current.isHidden():
            self.peer_list.setCurrentRow(-1)
            self.peer_view.clear()

    def _apply_hide_off(self) -> None:
        """Re-apply the row filters; when reasoning-off peers exist, refresh the open peer's pickers."""
        self._show_rows()
        current = self.peer_list.currentItem()
        if current is not None and self.ctx.reasoning_off.get(self._ws()):
            self._on_peer(current)

    def _on_peer(self, current, _previous=None) -> None:
        ws_item = self.workspace_list.currentItem()
        if current is None or ws_item is None:
            return
        ws = ws_item.data(Qt.UserRole)
        peers = self.ctx.visible_peers(ws, self.ctx.peers_cache.get(ws, []))
        self.peer_view.set_peer(ws, current.data(Qt.UserRole), peers)

    def _save_label(self) -> None:
        item = self.workspace_list.currentItem()
        if item is None:
            return
        ws = item.data(Qt.UserRole)
        if self.label_edit.text().strip() == self.store.label(ws):
            return
        self.store.set_label(ws, self.label_edit.text())
        item.setText(self.ctx.ws_title(ws))
        self.ctx.labels_changed.emit()

    def closeEvent(self, event) -> None:
        self.ctx.telemetry.stop()
        self.store.set_ui_value("geometry", bytes(self.saveGeometry().toBase64()).decode())
        super().closeEvent(event)
