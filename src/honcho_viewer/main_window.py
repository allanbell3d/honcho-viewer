"""Main window: connection bar, Explore tab (workspaces → peers → peer tabs) and the two Compare tabs."""
from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from . import __version__, render
from .async_call import Latest
from .client import HonchoClient
from .compare_peers_view import ComparePeersView
from .compare_reasoning_view import CompareReasoningView
from .compare_view import CompareView
from .monitor_view import MonitorView
from .peer_view import PeerView
from .store import LocalStore
from .widgets import AppContext, tip

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
        left_layout.addWidget(self.peer_list, 3)
        explore = QSplitter(Qt.Horizontal)
        explore.addWidget(left)
        explore.addWidget(self.peer_view)
        explore.setSizes([260, 1000])

        self.compare = CompareView(self.ctx)
        self.compare_peers = ComparePeersView(self.ctx)
        self.compare_reasoning = CompareReasoningView(self.ctx)
        self.monitor = MonitorView(self.ctx)
        self.tabs = QTabWidget()
        self.tabs.addTab(explore, "Explore")
        self.tabs.addTab(self.compare, "Compare models")
        self.tabs.addTab(self.compare_peers, "Compare peers")
        self.tabs.addTab(self.compare_reasoning, "Compare reasoning")
        self.tabs.addTab(self.monitor, "Monitor")

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
        self.ctx.peers_cache[ws] = [p["id"] for p in peers]
        self.peer_list.blockSignals(True)
        self.peer_list.clear()
        for p in peers:
            item = QListWidgetItem(p["id"])
            item.setData(Qt.UserRole, p["id"])
            item.setToolTip(json.dumps({k: p.get(k) for k in ("metadata", "configuration", "created_at")},
                                       indent=2, ensure_ascii=False))
            self.peer_list.addItem(item)
        self.peer_list.blockSignals(False)

    def _on_peer(self, current, _previous=None) -> None:
        ws_item = self.workspace_list.currentItem()
        if current is None or ws_item is None:
            return
        ws = ws_item.data(Qt.UserRole)
        self.peer_view.set_peer(ws, current.data(Qt.UserRole), self.ctx.peers_cache.get(ws, []))

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
