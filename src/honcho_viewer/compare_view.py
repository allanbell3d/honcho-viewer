"""Compare models tab: the same peer across several workspaces (e.g. same data, different models)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from . import explain
from .compare_panel import Column, ComparePanel
from .widgets import set_combo_items, tip


class CompareView(ComparePanel):
    HINT = explain.TAB_HINTS["compare"]
    ASK_PLACEHOLDER = "Ask every ticked workspace the same question…"
    FIRST_ROW = ("Model / label", "Your label for the workspace (edit it in the Explore tab).")

    def _build_selector(self, layout: QVBoxLayout) -> None:
        self.ws_list = tip(QListWidget(), "Tick the workspaces to compare.")
        self.ws_list.itemChanged.connect(self._on_checks)
        self.peer_combo = tip(QComboBox(), "Peers present in every ticked workspace.")
        self.load_btn = QPushButton("Load / refresh")
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self.load)
        layout.addWidget(QLabel("<b>Workspaces</b>"))
        layout.addWidget(self.ws_list, 1)
        layout.addWidget(QLabel("<b>Peer</b>"))
        layout.addWidget(self.peer_combo)
        layout.addWidget(self.load_btn)

    # ---- hooks for ComparePanel
    def _columns(self) -> list[Column]:
        peer = self.peer_combo.currentData()
        return [Column(ws, ws, peer) for ws in self.checked()] if peer else []

    def _title(self, col: Column) -> str:
        return self.ctx.ws_title(col.ws)

    def _first_cell(self, col: Column) -> str:
        return self.ctx.store.label(col.ws) or "–"

    def _report_head(self) -> dict:
        return {"peer": self._loaded[0].peer if self._loaded else None}

    def _report_subject(self) -> str:
        return self._loaded[0].peer if self._loaded else "peer"

    # ---- workspace / peer selection
    def set_workspaces(self, ids: list[str]) -> None:
        self.ws_list.blockSignals(True)
        self.ws_list.clear()
        for ws in ids:
            item = QListWidgetItem(self.ctx.ws_title(ws))
            item.setData(Qt.UserRole, ws)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.ws_list.addItem(item)
        self.ws_list.blockSignals(False)
        self._update_peers()

    def set_checked(self, ids: list[str]) -> None:
        self.ws_list.blockSignals(True)
        for i in range(self.ws_list.count()):
            item = self.ws_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in ids else Qt.Unchecked)
        self.ws_list.blockSignals(False)
        self._on_checks()

    def checked(self) -> list[str]:
        return [self.ws_list.item(i).data(Qt.UserRole) for i in range(self.ws_list.count())
                if self.ws_list.item(i).checkState() == Qt.Checked]

    def _on_checks(self, *_args) -> None:
        missing = [ws for ws in self.checked() if ws not in self.ctx.peers_cache]
        if not missing:
            self._update_peers()
            return

        def fetch(client):
            return {ws: [p["id"] for p in client.list_peers(ws)] for ws in missing}

        def done(found):
            self.ctx.peers_cache.update(found)
            self._update_peers()

        self.ctx.call(fetch, done, self.latest.ticket("peers"))

    def _update_peers(self) -> None:
        peer_sets = [set(self.ctx.peers_cache.get(ws, ())) for ws in self.checked()]
        common = sorted(set.intersection(*peer_sets)) if peer_sets else []
        set_combo_items(self.peer_combo, [(p, p) for p in common])
        self.load_btn.setEnabled(bool(common))

    def _relabel(self) -> None:
        self.ws_list.blockSignals(True)
        for i in range(self.ws_list.count()):
            item = self.ws_list.item(i)
            item.setText(self.ctx.ws_title(item.data(Qt.UserRole)))
        self.ws_list.blockSignals(False)
        self._refresh()
