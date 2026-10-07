"""Shared engine for every side-by-side Compare tab.

A compare tab shows N columns; each column is one (workspace, peer) pair. What differs between tabs is only
*which* pairs the user picks (several workspaces + one peer, or one workspace + several peers). Subclasses supply
the selector widgets and a few hooks; loading, Ask all, counts, column views and saving live here, once.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSplitter,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import explain, render
from .ask import build_chat_body
from .async_call import Latest
from .client import REASONING_LEVELS
from .widgets import AppContext, ColumnsView, hint, tip

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
    key: str  # unique within the tab: the workspace id or the peer id
    ws: str
    peer: str


class ComparePanel(QWidget):
    # ---- subclass contract
    HINT = ""
    ASK_PLACEHOLDER = "Ask every column the same question…"
    FIRST_ROW = ("Label", "")  # (row name, tooltip) of the first Counts row
    REPORT_KEY = "workspaces"  # key holding the per-column data in a saved report

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
        self.metrics = [self.FIRST_ROW] + list(COMMON_METRICS)

        self.show_combo = tip(QComboBox(), "What to put side by side.")
        self.show_combo.addItems(VIEWS)
        self.show_combo.currentTextChanged.connect(self._refresh)
        self.save_btn = tip(QPushButton("Save results"),
                            "Save everything loaded here (counts, answers, peer cards, representations, conclusions) "
                            "as a dated page in local\\comparisons, so you can compare with it later.")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_results)
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
        top.addWidget(self.save_btn)
        ask_row = QHBoxLayout()
        ask_row.addWidget(self.question, 1)
        ask_row.addWidget(tip(QLabel("reasoning"), explain.ASK_OPTIONS["reasoning_level"]))
        ask_row.addWidget(self.reasoning)
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
        self.results, self.answers, self.answer_data, self._asked = {}, {}, {}, None
        self.save_btn.setEnabled(True)
        self.latest.ticket("ask")  # answers for the previous selection are no longer wanted
        ok = self.latest.ticket("load")

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
        header = f"reasoning {body['reasoning_level']}"
        self._asked = {"question": body["query"], "reasoning": body["reasoning_level"]}
        self.answer_data = {}

        def answered(col, resp):
            self.answers[col.key] = render.answer_html(body["query"], resp, header)
            self.answer_data[col.key] = resp
            self._refresh()

        for col in self._loaded:
            self.answers[col.key] = render.placeholder("Honcho is thinking…")
            self.ctx.call(lambda c, col=col: c.chat(col.ws, col.peer, body),
                          lambda r, col=col: answered(col, r), ok)
        self.show_combo.setCurrentText("Answers")
        self._refresh()

    # ---- saving
    def save_results(self) -> None:
        names = [m for m, _ in self.metrics]
        data = {"saved_at": datetime.now().isoformat(timespec="seconds"), **self._report_head(),
                "question": (self._asked or {}).get("question"), "reasoning": (self._asked or {}).get("reasoning"),
                self.REPORT_KEY: {}}
        for col in self._loaded:
            r = self.results.get(col.key) or {}
            data[self.REPORT_KEY][col.key] = {
                "label": self.ctx.store.label(col.ws), "workspace": col.ws, "peer": col.peer,
                "counts": dict(zip(names, self._counts(col))), "answer": self.answer_data.get(col.key),
                "card": r.get("card"), "representation": r.get("rep"), "conclusions": r.get("conclusions") or []}
        path = self.ctx.store.save_comparison(render.comparison_report_html(data, names), self._report_subject())
        missing = [c.key for c in self._loaded
                   if c.key not in self.results or (self._asked and c.key not in self.answer_data)]
        note = f" (still loading: {', '.join(missing)})" if missing else ""
        self.ctx.status.emit(f"Saved to {path}{note}")

    # ---- display
    def _counts(self, col: Column) -> list[str]:
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
        if view == "Counts":
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
