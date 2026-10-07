"""Compare peers tab: several peers of ONE workspace, side by side (ask them all the same question)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)

from . import explain
from .compare_panel import Column, ComparePanel
from .widgets import set_combo_items, tip

UI_KEY = "peers_workspace"


class ComparePeersView(ComparePanel):
    HINT = explain.TAB_HINTS["compare_peers"]
    ASK_PLACEHOLDER = "Ask every ticked peer the same question, e.g. what do you know about me?"
    FIRST_ROW = ("Peer", "The peer in this column.")
    REPORT_KEY = "peers"

    def _build_selector(self, layout: QVBoxLayout) -> None:
        self.ws_combo = tip(QComboBox(), "The workspace whose peers you want to compare.")
        self.ws_combo.currentIndexChanged.connect(self._on_workspace)
        self.peer_list = tip(QListWidget(), "Tick the peers to compare. They are all ticked to start with.")
        self.peer_list.itemChanged.connect(self._on_ticks)
        self.all_btn = QPushButton("All")
        self.none_btn = QPushButton("None")
        self.all_btn.clicked.connect(lambda: self._tick_all(True))
        self.none_btn.clicked.connect(lambda: self._tick_all(False))
        self.load_btn = QPushButton("Load / refresh")
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self.load)
        buttons = QHBoxLayout()
        buttons.addWidget(self.all_btn)
        buttons.addWidget(self.none_btn)
        layout.addWidget(QLabel("<b>Workspace</b>"))
        layout.addWidget(self.ws_combo)
        layout.addWidget(QLabel("<b>Peers</b>"))
        layout.addWidget(self.peer_list, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.load_btn)

    # ---- hooks for ComparePanel
    def _columns(self) -> list[Column]:
        ws = self.ws_combo.currentData()
        return [Column(p, ws, p) for p in self.checked()] if ws else []

    def _title(self, col: Column) -> str:
        return col.peer

    def _first_cell(self, col: Column) -> str:
        return col.peer

    def _report_head(self) -> dict:
        return {"workspace": self._loaded[0].ws if self._loaded else None}

    def _report_subject(self) -> str:
        return self._loaded[0].ws if self._loaded else "workspace"

    # ---- workspace / peer selection
    def set_workspaces(self, ids: list[str]) -> None:
        wanted = self.ws_combo.currentData() or self.ctx.store.ui_value(UI_KEY)
        set_combo_items(self.ws_combo, [(self.ctx.ws_title(ws), ws) for ws in ids], keep_current=False)
        index = self.ws_combo.findData(wanted)
        self.ws_combo.setCurrentIndex(index if index >= 0 else 0)
        self._on_workspace()

    def checked(self) -> list[str]:
        return [self.peer_list.item(i).data(Qt.UserRole) for i in range(self.peer_list.count())
                if self.peer_list.item(i).checkState() == Qt.Checked]

    def _on_workspace(self, *_args) -> None:
        ws = self.ws_combo.currentData()
        self.peer_list.clear()
        self.load_btn.setEnabled(False)
        if not ws:
            return
        self.ctx.store.set_ui_value(UI_KEY, ws)
        if ws in self.ctx.peers_cache:
            self._show_peers(ws, self.ctx.peers_cache[ws])
            return
        self.ctx.call(lambda c: [p["id"] for p in c.list_peers(ws)],
                      lambda ids: self._cached_then_show(ws, ids), self.latest.ticket("peers"))

    def _cached_then_show(self, ws: str, ids: list[str]) -> None:
        self.ctx.peers_cache[ws] = ids
        self._show_peers(ws, ids)

    def _show_peers(self, ws: str, ids: list[str]) -> None:
        if ws != self.ws_combo.currentData():
            return
        self.peer_list.blockSignals(True)
        self.peer_list.clear()
        for peer in sorted(ids):
            item = QListWidgetItem(peer)
            item.setData(Qt.UserRole, peer)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.peer_list.addItem(item)
        self.peer_list.blockSignals(False)
        self._on_ticks()

    def _tick_all(self, on: bool) -> None:
        self.peer_list.blockSignals(True)
        for i in range(self.peer_list.count()):
            self.peer_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.peer_list.blockSignals(False)
        self._on_ticks()

    def set_checked(self, peers: list[str]) -> None:
        self.peer_list.blockSignals(True)
        for i in range(self.peer_list.count()):
            item = self.peer_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in peers else Qt.Unchecked)
        self.peer_list.blockSignals(False)
        self._on_ticks()

    def _on_ticks(self, *_args) -> None:
        self.load_btn.setEnabled(bool(self.checked()))

    def _relabel(self) -> None:
        current = self.ws_combo.currentData()
        self.ws_combo.blockSignals(True)
        for i in range(self.ws_combo.count()):
            self.ws_combo.setItemText(i, self.ctx.ws_title(self.ws_combo.itemData(i)))
        self.ws_combo.blockSignals(False)
        self._refresh()
