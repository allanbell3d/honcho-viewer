"""Shared engine for every side-by-side Compare tab.

A compare tab shows N columns; each column is one (workspace, peer) pair. What differs between tabs is only
*which* pairs the user picks (several workspaces + one peer, or one workspace + several peers). Subclasses supply
the selector widgets and a few hooks; loading, Ask all, counts, column views and saving live here, once.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import explain, render
from .ask import build_chat_body
from .async_call import Latest
from .client import REASONING_LEVELS
from .widgets import AppContext, ColumnsView, hint, set_combo_items, tip

COMMON_METRICS = (
    ("Conclusions", "All conclusions about this peer."),
    ("  from messages", explain.LEVELS["explicit"][1]),
    ("  deductive (dream)", explain.LEVELS["deductive"][1]),
    ("  inductive (dream)", explain.LEVELS["inductive"][1]),
    ("  contradiction (dream)", explain.LEVELS["contradiction"][1]),
    ("Peer card facts", "Number of facts on the peer card."),
    ("Representation length", "Size of Honcho's written summary of the peer."),
    ("Rated by you", "How many of these conclusions you rated in the Conclusions tab."),
    ("Accuracy", "Correct ÷ (correct + wrong) of the ones you rated. 'Unsure' is not counted."),
    ("Processing queue", "Background work still pending. Compare only when every workspace is idle."),
)
VIEWS = ("Counts", "Peer card", "Representation", "Conclusions", "Answers")


class Column(NamedTuple):
    key: str  # unique within the tab: the workspace id, the peer id or the reasoning level
    ws: str
    peer: str
    level: str | None = None  # set only by tabs that vary the reasoning level


class ComparePanel(QWidget):
    # ---- subclass contract
    HINT = ""
    ASK_PLACEHOLDER = "Ask every column the same question…"
    FIRST_ROW = ("Label", "")  # (row name, tooltip) of the first Counts row
    REPORT_KEY = "workspaces"  # key holding the per-column data in a saved report
    VIEWS = VIEWS
    TABLE_VIEW = "Counts"  # which entry of VIEWS shows the table instead of columns
    METRICS = COMMON_METRICS  # rows after FIRST_ROW in the table
    LOADS_DETAILS = True  # fetch card / representation / conclusions / queue for every column
    HAS_REASONING = True  # show the single reasoning-level picker next to the question

    def _build_selector(self, layout: QVBoxLayout) -> None:
        """Add the selection widgets (and ``self.load_btn``) to the left-hand column."""
        raise NotImplementedError

    def _columns(self) -> list[Column]:
        """The columns the current selection describes (may be empty)."""
        raise NotImplementedError

    def _title(self, col: Column) -> str:
        raise NotImplementedError

    def _first_cell(self, col: Column) -> str:
        raise NotImplementedError

    def _report_head(self) -> dict:
        """Extra top-level fields for a saved report (what was held constant: the peer, or the workspace)."""
        return {}

    def _report_subject(self) -> str:
        return "comparison"

    def _report_column_extra(self, col: Column) -> dict:
        return {}

    # ---- construction
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.latest = Latest()
        self.results: dict[str, dict] = {}
        self.answers: dict[str, str] = {}
        self.answer_data: dict[str, dict] = {}
        self._asked: dict | None = None
        self._loaded: list[Column] = []
        self._column_titles: list[str] = []
        self._opened: dict | None = None  # a saved report shown instead of live data
        self.metrics = [self.FIRST_ROW] + list(self.METRICS)

        self.show_combo = tip(QComboBox(), "What to put side by side.")
        self.show_combo.addItems(self.VIEWS)
        self.show_combo.currentTextChanged.connect(self._refresh)
        self.save_btn = tip(QPushButton("Save results"),
                            "Save everything loaded here (counts, answers, peer cards, representations, conclusions) "
                            "as a dated page in local\\comparisons, so you can compare with it later.")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_results)
        self.open_btn = tip(QPushButton("Open saved results…"),
                            "Show a page you saved earlier with Save results, exactly as it was: counts or stats, "
                            "answers, telemetry and everything else it contains. Works without a server connection.")
        self.open_btn.clicked.connect(self.open_saved)
        self.counts_table = QTableWidget(len(self.metrics), 0)
        self.counts_table.setVerticalHeaderLabels([m for m, _ in self.metrics])
        for row, (_, why) in enumerate(self.metrics):
            self.counts_table.verticalHeaderItem(row).setToolTip(why)
        self.counts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.columns = ColumnsView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.counts_table)
        self.stack.addWidget(self.columns)

        self.question = tip(QLineEdit(placeholderText=self.ASK_PLACEHOLDER), explain.ASK_OPTIONS["query"])
        self.question.returnPressed.connect(self.ask_all)
        self.reasoning = tip(QComboBox(), explain.ASK_OPTIONS["reasoning_level"])
        self.reasoning.addItems(REASONING_LEVELS)
        self.reasoning.setCurrentText("low")
        self.evidence = tip(QCheckBox("evidence"), explain.ASK_OPTIONS["include_evidence"])
        self.evidence.setChecked(True)
        self.ask_btn = QPushButton("Ask all")
        self.ask_btn.clicked.connect(self.ask_all)

        left = QWidget()
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(hint(self.HINT))
        self._build_selector(left_layout)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.show_combo)
        top.addStretch()
        top.addWidget(self.open_btn)
        top.addWidget(self.save_btn)
        ask_row = QHBoxLayout()
        ask_row.addWidget(self.question, 1)
        self.reasoning_label = tip(QLabel("reasoning"), explain.ASK_OPTIONS["reasoning_level"])
        ask_row.addWidget(self.reasoning_label)
        ask_row.addWidget(self.reasoning)
        if not self.HAS_REASONING:
            self.reasoning_label.hide()
            self.reasoning.hide()
        ask_row.addWidget(self.evidence)
        ask_row.addWidget(self.ask_btn)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(top)
        right_layout.addWidget(self.stack, 1)
        right_layout.addLayout(ask_row)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([240, 960])
        QVBoxLayout(self).addWidget(split)
        ctx.labels_changed.connect(self._relabel)

    def _relabel(self) -> None:
        self._refresh()

    # ---- loading
    def load(self) -> None:
        cols = self._columns()
        if not cols:
            self.ctx.status.emit("Pick what to compare first (tick at least one item).")
            return
        self._loaded = cols
        self._opened = None
        self.results, self.answers, self.answer_data, self._asked = {}, {}, {}, None
        self.save_btn.setEnabled(True)
        self.latest.ticket("ask")  # answers for the previous selection are no longer wanted
        ok = self.latest.ticket("load")
        if not self.LOADS_DETAILS:
            self._refresh()
            return

        def gather(client, col):
            # observer == observed: the peer's own view (other observers keep duplicate copies)
            return {"conclusions": client.list_conclusions(col.ws, observer=col.peer, observed=col.peer),
                    "card": client.peer_card(col.ws, col.peer),
                    "rep": client.representation(col.ws, col.peer),
                    "queue": client.queue_status(col.ws)}

        def got(col, result):
            self.results[col.key] = result
            self._refresh()

        for col in cols:
            self.ctx.call(lambda c, col=col: gather(c, col), lambda r, col=col: got(col, r), ok)
        self._refresh()

    def ask_all(self) -> None:
        body, error = build_chat_body(self.question.text(), self.reasoning.currentText(), self.evidence.isChecked())
        if error:
            self.ctx.status.emit(error)
            return
        if self._columns() != self._loaded:
            self.load()
        if not self._loaded:
            return
        ok = self.latest.ticket("ask")  # a newer ask or load supersedes this one
        self._opened = None  # live results replace any saved page being shown
        self.save_btn.setEnabled(True)
        self._asked = {"question": body["query"], "reasoning": self._asked_reasoning(body)}
        self.answer_data = {}
        self._start_asks(body, ok)
        self.show_combo.setCurrentText("Answers")
        self._refresh()

    def _asked_reasoning(self, body: dict) -> str:
        return body["reasoning_level"]

    def _answer_header(self, col: Column, body: dict) -> str:
        return f"reasoning {body['reasoning_level']}"

    def _set_answer(self, col: Column, body: dict, resp: dict) -> None:
        self.answers[col.key] = render.answer_html(body["query"], resp, self._answer_header(col, body))
        self.answer_data[col.key] = resp
        self._refresh()

    def _start_asks(self, body: dict, ok) -> None:
        """Send the question to every column at once. Tabs that need another order override this."""
        for col in self._loaded:
            self.answers[col.key] = render.placeholder("Honcho is thinking…")
            self.ctx.call(lambda c, col=col: c.chat(col.ws, col.peer, body),
                          lambda r, col=col: self._set_answer(col, body, r), ok)

    # ---- saving
    def save_results(self) -> None:
        names = [m for m, _ in self.metrics]
        data = {"saved_at": datetime.now().isoformat(timespec="seconds"), **self._report_head(),
                "question": (self._asked or {}).get("question"), "reasoning": (self._asked or {}).get("reasoning"),
                self.REPORT_KEY: {}}
        for col in self._loaded:
            r = self.results.get(col.key) or {}
            entry = {"label": self.ctx.store.label(col.ws), "workspace": col.ws, "peer": col.peer,
                     "counts": dict(zip(names, self._counts(col))), "answer": self.answer_data.get(col.key)}
            if self.LOADS_DETAILS:
                entry.update(card=r.get("card"), representation=r.get("rep"), conclusions=r.get("conclusions") or [])
            data[self.REPORT_KEY][col.key] = {**entry, **self._report_column_extra(col)}
        default = self.ctx.store.comparison_default_path(self._report_subject())
        path = self.ctx.pick_save_path(self, "Save results", default, "Saved comparison (*.html)", "comparisons")
        if path is None:
            self.ctx.status.emit("Not saved.")
            return
        try:
            self.ctx.store.write_comparison(path, render.comparison_report_html(data, names))
        except OSError as exc:
            self.ctx.status.emit(f"Could not save to {path}: {exc}")
            return
        missing = [c.key for c in self._loaded
                   if (self.LOADS_DETAILS and c.key not in self.results)
                   or (self._asked and c.key not in self.answer_data)]
        note = f" (still loading: {', '.join(missing)})" if missing else ""
        self.ctx.status.emit(f"Saved to {path}{note}")

    # ---- opening a saved page
    def open_saved(self) -> None:
        path = self.ctx.pick_open_path(self, "Open saved results", self.ctx.store.root / "comparisons",
                                       "Saved comparison (*.html)", "comparisons")
        if path is None:
            return
        try:
            data = render.parse_comparison_html(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            self.ctx.status.emit(f"Could not read {path}: {exc}")
            return
        if data is None:
            self.ctx.status.emit(f"{Path(path).name} is not a page saved by Honcho Viewer (no data inside).")
            return
        if self.REPORT_KEY not in data:
            kinds = {"workspaces": "Compare models", "peers": "Compare peers", "levels": "Compare reasoning"}
            kind = next((name for key, name in kinds.items() if key in data), "another kind of")
            self.ctx.status.emit(f"{Path(path).name} was saved from {kind}: open it in that tab.")
            return
        self._apply_saved(data, Path(path).name)

    def _apply_saved(self, data: dict, name: str) -> None:
        self.latest.ticket("ask")  # drop replies still on their way for the previous contents
        self.latest.ticket("load")
        entries = data[self.REPORT_KEY]
        question = data.get("question") or ""
        self._opened = data
        self._loaded = [Column(key, e.get("workspace") or "", e.get("peer") or "", e.get("level"))
                        for key, e in entries.items()]
        self.results, self.answers, self.answer_data = {}, {}, {}
        for col in self._loaded:
            e = entries[col.key]
            self.results[col.key] = {"conclusions": e.get("conclusions") or [], "card": e.get("card") or [],
                                     "rep": e.get("representation") or "", "queue": {}}
            if e.get("answer"):
                self.answer_data[col.key] = e["answer"]
                header = self._answer_header(col, {"reasoning_level": data.get("reasoning")})
                self.answers[col.key] = render.answer_html(question, e["answer"], header)
        self._asked = {"question": question, "reasoning": data.get("reasoning")} if question else None
        self._restore_saved(data)
        self.save_btn.setEnabled(False)
        self.show_combo.setCurrentText(self.TABLE_VIEW)
        self._refresh()
        self.ctx.status.emit(f"Opened {name}, saved {render.short_time(data.get('saved_at', ''))}"
                             + (f" · question: {question}" if question else ""))

    def _restore_saved(self, data: dict) -> None:
        """Hook for tabs that keep more state than the columns (the reasoning tab restores its telemetry)."""

    # ---- display
    def _counts(self, col: Column) -> list[str]:
        if self._opened is not None:  # a saved page: show its numbers as they were
            saved = (self._opened[self.REPORT_KEY].get(col.key) or {}).get("counts", {})
            return [str(saved.get(name, "")) for name, _ in self.metrics]
        return self._live_counts(col)

    def _live_counts(self, col: Column) -> list[str]:
        first = self._first_cell(col)
        r = self.results.get(col.key)
        if r is None:
            return [first] + ["…"] * (len(self.metrics) - 1)
        rows = r["conclusions"]
        by_level = Counter(c.get("level") for c in rows)
        acc = self.ctx.store.accuracy(col.ws, ids={c["id"] for c in rows})
        return [first, str(len(rows)), str(by_level["explicit"]), str(by_level["deductive"]),
                str(by_level["inductive"]), str(by_level["contradiction"]), str(len(r["card"] or [])),
                f"{len(r['rep'])} chars", str(acc.rated), f"{acc.percent}%" if acc.percent is not None else "–",
                render.queue_text(r["queue"])]

    def _column_html(self, col: Column, view: str) -> str:
        if view == "Answers":
            return self.answers.get(col.key, render.placeholder("Type a question below and press Ask all."))
        r = self.results.get(col.key)
        if r is None:
            return render.placeholder("Loading…")
        if view == "Peer card":
            return render.card_html(r["card"])
        if view == "Representation":
            return render.text_html(r["rep"], "No representation yet.")
        ratings = {c["id"]: self.ctx.store.rating(col.ws, c["id"]) for c in r["conclusions"]}
        return render.conclusions_html(r["conclusions"], ratings)

    def _refresh(self, *_args) -> None:
        view = self.show_combo.currentText()
        if view == self.TABLE_VIEW:
            self.stack.setCurrentWidget(self.counts_table)
            self.counts_table.setColumnCount(len(self._loaded))
            self.counts_table.setHorizontalHeaderLabels([c.key for c in self._loaded])
            for i, col in enumerate(self._loaded):
                for row, value in enumerate(self._counts(col)):
                    self.counts_table.setItem(row, i, QTableWidgetItem(value))
            self.counts_table.resizeColumnsToContents()
            return
        self.stack.setCurrentWidget(self.columns)
        titles = [self._title(c) for c in self._loaded]
        if titles != self._column_titles:
            self.columns.set_columns(titles)
            self._column_titles = titles
        for i, col in enumerate(self._loaded):
            self.columns.set_html(i, self._column_html(col, view))

    def cell(self, metric: str, column: int) -> str:
        row = [m for m, _ in self.metrics].index(metric)
        item = self.counts_table.item(row, column)
        return item.text() if item else ""


class WorkspaceMixin:
    """A workspace dropdown that loads the chosen workspace's peers. For tabs that compare within ONE workspace.

    The tab calls ``_make_workspace_combo()`` while building its selector and provides ``_clear_peers()`` and
    ``_fill_peers(ids)`` to show the peers however it likes (a checklist, a dropdown, ...).
    """

    UI_KEY = "peers_workspace"  # remembered between runs

    def _make_workspace_combo(self) -> QComboBox:
        self.ws_combo = tip(QComboBox(), "The workspace whose peers you want to compare.")
        self.ws_combo.currentIndexChanged.connect(self._on_workspace)
        return self.ws_combo

    def _clear_peers(self) -> None:
        raise NotImplementedError

    def _fill_peers(self, ids: list[str]) -> None:
        raise NotImplementedError

    def set_workspaces(self, ids: list[str]) -> None:
        wanted = self.ws_combo.currentData() or self.ctx.store.ui_value(self.UI_KEY)
        set_combo_items(self.ws_combo, [(self.ctx.ws_title(ws), ws) for ws in ids], keep_current=False)
        index = self.ws_combo.findData(wanted)
        self.ws_combo.setCurrentIndex(index if index >= 0 else 0)
        self._on_workspace()

    def _on_workspace(self, *_args) -> None:
        ws = self.ws_combo.currentData()
        self._clear_peers()
        if not ws:
            return
        self.ctx.store.set_ui_value(self.UI_KEY, ws)
        if ws in self.ctx.peers_cache:
            self._show_peers(ws, self.ctx.peers_cache[ws])
            return
        self.ctx.call(lambda c: [p["id"] for p in c.list_peers(ws)],
                      lambda ids: self._cache_then_show(ws, ids), self.latest.ticket("peers"))

    def _cache_then_show(self, ws: str, ids: list[str]) -> None:
        self.ctx.peers_cache[ws] = ids
        self._show_peers(ws, ids)

    def _show_peers(self, ws: str, ids: list[str]) -> None:
        if ws == self.ws_combo.currentData():  # ignore a late reply for a workspace we already left
            self._fill_peers(sorted(ids))

    def _relabel_workspaces(self) -> None:
        self.ws_combo.blockSignals(True)
        for i in range(self.ws_combo.count()):
            self.ws_combo.setItemText(i, self.ctx.ws_title(self.ws_combo.itemData(i)))
        self.ws_combo.blockSignals(False)
