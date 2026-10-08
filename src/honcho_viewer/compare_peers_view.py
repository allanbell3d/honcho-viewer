"""Compare peers tab: several peers of ONE workspace, side by side (ask them all the same question)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)

from . import explain
from .compare_panel import Column, ComparePanel, WorkspaceMixin
from .widgets import tip


class ComparePeersView(WorkspaceMixin, ComparePanel):
    HINT = explain.TAB_HINTS["compare_peers"]
    ASK_PLACEHOLDER = "Ask every ticked peer the same question, e.g. what do you know about me?"
    FIRST_ROW = ("Peer", "The peer in this column.")
    REPORT_KEY = "peers"

    def _build_selector(self, layout: QVBoxLayout) -> None:
        self._make_workspace_combo()
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
    def checked(self) -> list[str]:
        return [self.peer_list.item(i).data(Qt.UserRole) for i in range(self.peer_list.count())
                if self.peer_list.item(i).checkState() == Qt.Checked]

    def _clear_peers(self) -> None:
        self.peer_list.clear()
        self.load_btn.setEnabled(False)

    def _fill_peers(self, ids: list[str]) -> None:
        self.peer_list.blockSignals(True)
        self.peer_list.clear()
        for peer in ids:
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
        self._relabel_workspaces()
        self._refresh()
