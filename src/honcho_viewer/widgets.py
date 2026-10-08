"""Small shared widgets and the AppContext every panel talks through."""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFileDialog, QLabel, QSizePolicy, QSplitter, QTextBrowser, QVBoxLayout,
                               QWidget)

from .async_call import run_async
from .client import HonchoClient, HonchoError
from .store import LocalStore
from .telemetry_hub import TelemetryHub


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, HonchoError):
        if exc.status == 401:
            return "Token rejected (401). Paste a valid token and press Connect."
        if exc.status == 0:
            return exc.detail
        return f"Honcho error {exc.status}: {exc.detail}"
    return f"Unexpected error: {exc!r}"


class AppContext(QObject):
    status = Signal(str)
    labels_changed = Signal()

    def __init__(self, store: LocalStore):
        super().__init__()
        self.store = store
        self.client: HonchoClient | None = None
        self.peers_cache: dict[str, list[str]] = {}
        self.telemetry = TelemetryHub(store)

    def call(self, fn: Callable[[HonchoClient], Any], on_done: Callable[[Any], None],
             is_current: Callable[[], bool] = lambda: True,
             on_error: Callable[[Exception], None] | None = None) -> None:
        """Run ``fn(client)`` in the background; drop the reply if ``is_current()`` turned false."""
        client = self.client
        if client is None:
            self.status.emit("Not connected. Enter the server URL and token, then press Connect.")
            return

        def done(result):
            if is_current():
                on_done(result)

        def failed(exc):
            if is_current():
                (on_error or (lambda e: None))(exc)
                self.status.emit(friendly_error(exc))

        run_async(lambda: fn(client), done, failed)

    def pick_save_path(self, parent, title: str, default: Path, name_filter: str, kind: str) -> Path | None:
        """Ask where to save. Starts at ``default`` (or the folder you last used for this ``kind``); None = cancelled."""
        last = self.store.ui_value(f"save_dir_{kind}", "")
        start = Path(last) / default.name if last and Path(last).is_dir() else default
        chosen, _ = QFileDialog.getSaveFileName(parent, title, str(start), name_filter)
        if not chosen:
            return None
        self.store.set_ui_value(f"save_dir_{kind}", str(Path(chosen).parent))
        return Path(chosen)

    def pick_open_path(self, parent, title: str, default_folder: Path, name_filter: str, kind: str) -> Path | None:
        last = self.store.ui_value(f"save_dir_{kind}", "")
        folder = Path(last) if last and Path(last).is_dir() else default_folder
        chosen, _ = QFileDialog.getOpenFileName(parent, title, str(folder), name_filter)
        return Path(chosen) if chosen else None

    def ws_title(self, ws: str) -> str:
        label = self.store.label(ws)
        return f"{ws}  ·  {label}" if label else ws


class HtmlView(QTextBrowser):
    def __init__(self, html: str = ""):
        super().__init__()
        self.setOpenLinks(False)
        if html:
            self.setHtml(html)


class _HintLabel(QLabel):
    """Wrapped label sized for its real width (QLabel's default guesses a narrow width and over-reserves height)."""

    def sizeHint(self):
        size = super().sizeHint()
        if self.width() > 0:
            size.setHeight(self.heightForWidth(self.width()))
        return size

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()


def hint(text: str) -> QLabel:
    label = _HintLabel(text)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)  # never soak up spare height
    label.setStyleSheet("color: palette(mid); font-style: italic; padding: 2px 0 6px 0;")
    return label


def tip(widget: QWidget, text: str) -> QWidget:
    widget.setToolTip(text)
    return widget


def set_combo_items(combo: QComboBox, items: list[tuple[str, Any]], keep_current: bool = True) -> None:
    """Replace (text, data) items without firing change signals; keeps the selection when possible."""
    current = combo.currentData() if keep_current else None
    combo.blockSignals(True)
    combo.clear()
    for text, data in items:
        combo.addItem(text, data)
    index = combo.findData(current) if current is not None else -1
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


class ColumnsView(QWidget):
    """N side-by-side read-only HTML panes with a title each (one per workspace)."""

    def __init__(self):
        super().__init__()
        self._splitter = QSplitter(Qt.Horizontal)
        self._views: list[HtmlView] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._splitter)

    def set_columns(self, titles: list[str]) -> None:
        for i in reversed(range(self._splitter.count())):
            self._splitter.widget(i).deleteLater()
        self._views = []
        for title in titles:
            box = QWidget()
            col = QVBoxLayout(box)
            col.setContentsMargins(2, 0, 2, 0)
            head = QLabel(f"<b>{escape(title)}</b>")
            col.addWidget(head)
            view = HtmlView()
            col.addWidget(view)
            self._views.append(view)
            self._splitter.addWidget(box)

    def set_html(self, index: int, html: str) -> None:
        if index < len(self._views):
            self._views[index].setHtml(html)

    def text(self, index: int) -> str:
        return self._views[index].toPlainText()
