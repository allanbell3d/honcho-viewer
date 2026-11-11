"""Scopes tab: a workspace's scopes, their member sessions, and who in those sessions is observed by whom.

Read-only. A scope is a named group of sessions that bounds what recall can see; Honcho keeps it as a hidden
observer of its member sessions. Inside each session, every peer has two switches: observe_me (Honcho writes
conclusions about this peer from the session) and observe_others (this peer keeps its own view of the others).
Honcho does not list the keys it issued, so who holds a key for a scope cannot be shown here.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from . import render
from .async_call import Latest
from .client import HonchoError
from .widgets import AppContext, HtmlView, copyable, hint, set_combo_items, tip

DETAIL_SESSIONS = 50  # sessions whose peers are read when a scope is opened; "Show all" reads the rest
ANY_PEER = ""
HELP = ("A scope is a named group of sessions that limits what recall can see. Pick a scope to see its sessions and, "
        "in each, who Honcho observes. Honcho does not list the keys it issued, so who holds a key is not shown.")


def _switch(value) -> str:
    return "not set" if value is None else "on" if value else "off"


def _visibility(ws: str, client, sessions: list[dict], peer_configs: dict[str, dict]) -> list[dict]:
    """For each session: its peers with their session-level observe_me / observe_others."""
    rows = []
    for session in sessions:
        sid = session["id"]
        peers = []
        for p in client.session_peers(ws, sid):
            config = client.session_peer_config(ws, sid, p["id"])
            peers.append({"id": p["id"], "observe_me": config.get("observe_me"),
                          "observe_others": config.get("observe_others"),
                          "peer_observe_me": (peer_configs.get(p["id"]) or {}).get("observe_me")})
        rows.append({"id": sid, "created_at": session.get("created_at"), "peers": peers})
    return rows


class ScopesView(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.latest = Latest()
        self._scopes: list[dict] = []
        self._members: dict[str, list[dict]] = {}  # scope -> member sessions
        self._peer_configs: dict[str, dict] = {}  # peer -> workspace-level configuration
        self._peer_sessions: set[str] | None = None  # sessions of the peer picked in "Peer", None = any peer

        self.ws_combo = tip(QComboBox(), "The workspace whose scopes to show.")
        self.ws_combo.setMinimumContentsLength(18)
        self.ws_combo.currentIndexChanged.connect(self.load)
        self.peer_combo = tip(QComboBox(), "Only scopes that include sessions this peer took part in.")
        self.peer_combo.setMinimumContentsLength(14)
        self.peer_combo.currentIndexChanged.connect(self._peer_changed)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.load)
        self.scope_list = tip(QListWidget(), "Scopes in this workspace, with their number of sessions.")
        self.scope_list.currentItemChanged.connect(lambda item, _prev=None: self._open_scope(item))
        copyable(self.scope_list)
        self.show_all = QPushButton("Show all sessions")
        self.show_all.setVisible(False)
        self.show_all.clicked.connect(lambda: self._open_scope(self.scope_list.currentItem(), limit=None))
        self.detail = HtmlView(render.placeholder("Pick a workspace."))

        top = QHBoxLayout()
        top.addWidget(QLabel("Workspace"))
        top.addWidget(self.ws_combo)
        top.addWidget(QLabel("Peer"))
        top.addWidget(self.peer_combo)
        top.addStretch(1)
        top.addWidget(refresh)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.detail, 1)
        right_layout.addWidget(self.show_all, 0, Qt.AlignLeft)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.scope_list)
        split.addWidget(right)
        split.setSizes([260, 900])
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(hint(HELP))
        layout.addWidget(split, 1)

    def set_workspaces(self, names: list[str]) -> None:
        set_combo_items(self.ws_combo, [(self.ctx.ws_title(n), n) for n in names])
        self.load()

    # ---- loading
    def load(self) -> None:
        """Read the workspace's scopes, each scope's member sessions and the peers (for the Peer picker)."""
        ws = self.ws_combo.currentData()
        if not ws:
            return
        self.scope_list.clear()
        self.show_all.setVisible(False)
        self.detail.setHtml(render.placeholder("Loading scopes…"))

        def read(client):
            scopes = client.list_scopes(ws)
            return scopes, {s["id"]: client.scope_sessions(ws, s["id"]) for s in scopes}, client.list_peers(ws)

        self.ctx.call(read, lambda result: self._show_scopes(ws, *result), self.latest.ticket("ws"),
                      on_error=self._no_scopes)

    def _no_scopes(self, exc: Exception) -> None:
        if isinstance(exc, HonchoError) and exc.status == 404:
            self.detail.setHtml(render.placeholder("This Honcho server has no scopes (they came with Honcho 3.5)."))
        else:
            self.detail.setHtml(render.placeholder(f"Could not read scopes: {exc}"))

    def _show_scopes(self, ws: str, scopes: list[dict], members: dict[str, list[dict]], peers: list[dict]) -> None:
        self._scopes, self._members = scopes, members
        self._peer_configs = {p["id"]: p.get("configuration") or {} for p in peers}
        set_combo_items(self.peer_combo, [("(any peer)", ANY_PEER)] + [(p["id"], p["id"]) for p in peers])
        self._peer_sessions = None
        self.peer_combo.setCurrentIndex(0)
        self._fill_list()
        self.detail.setHtml(render.placeholder("No scopes in this workspace." if not scopes else "Pick a scope."))

    def _peer_changed(self) -> None:
        ws, peer = self.ws_combo.currentData(), self.peer_combo.currentData()
        if not peer:
            self._peer_sessions = None
            self._fill_list()
            return
        self.ctx.call(lambda c: c.peer_sessions(ws, peer), self._got_peer_sessions, self.latest.ticket("peer"))

    def _got_peer_sessions(self, sessions: list[dict]) -> None:
        self._peer_sessions = {s["id"] for s in sessions}
        self._fill_list()

    def _fill_list(self) -> None:
        """List the scopes; with a peer picked, only those holding its sessions, with how many."""
        self.scope_list.clear()
        for scope in self._scopes:
            name = scope["id"]
            ids = {s["id"] for s in self._members.get(name, [])}
            count = scope.get("session_count")
            text = f"{name}  ·  {count if count is not None else len(ids)} sessions"
            if self._peer_sessions is not None:
                shared = len(ids & self._peer_sessions)
                if not shared:
                    continue
                text += f"  ·  {shared} with {self.peer_combo.currentData()}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, name)
            self.scope_list.addItem(item)

    # ---- one scope
    def _open_scope(self, item, limit: int | None = DETAIL_SESSIONS) -> None:
        if item is None:
            return
        ws, name = self.ws_combo.currentData(), item.data(Qt.UserRole)
        sessions = self._members.get(name, [])
        if self._peer_sessions is not None:
            sessions = [s for s in sessions if s["id"] in self._peer_sessions]
        shown = sessions if limit is None else sessions[:limit]
        self.show_all.setVisible(len(shown) < len(sessions))
        self.show_all.setText(f"Show all {len(sessions)} sessions")
        self.detail.setHtml(render.placeholder(f"Reading who is observed in {len(shown)} session(s)…"))
        configs = dict(self._peer_configs)

        def read(client):
            return client.scope_status(ws, name), _visibility(ws, client, shown, configs)

        self.ctx.call(read, lambda result: self.detail.setHtml(self.scope_html(name, len(sessions), *result)),
                      self.latest.ticket("scope"))

    def scope_html(self, name: str, total: int, status: dict, rows: list[dict]) -> str:
        e = render.esc
        out = [f"<h3>Scope {e(name)}</h3><p>{total} session(s)"
               + (f", {len(rows)} shown" if len(rows) < total else "")
               + ". Recall confined to this scope sees what Honcho learned from these sessions only.</p>",
               "<p><b>observes me</b>: Honcho writes conclusions about this peer from the session. "
               "<b>observes others</b>: this peer keeps its own view of the other peers. "
               "<i>not set</i>: the session leaves it to the peer's own setting (shown in brackets).</p>",
               "<table border='1' cellspacing='0' cellpadding='3'><tr><th>Session</th><th>Backfill</th>"
               "<th>Peer</th><th>observes me</th><th>observes others</th></tr>"]
        for row in rows:
            state = status.get(row["id"]) or {}
            backfill = e(str(state.get("state", "")))
            if state.get("docs_copied") is not None:
                backfill += f" · {state['docs_copied']} copied"
            peers = row["peers"] or [{"id": "(no peers)", "observe_me": None, "observe_others": None,
                                      "peer_observe_me": None}]
            for i, p in enumerate(peers):
                me = _switch(p["observe_me"])
                if p["observe_me"] is None and p["id"] != "(no peers)":
                    me += f" ({_switch(p['peer_observe_me'])})"
                lead = (f"<td rowspan='{len(peers)}'>{e(row['id'])}</td><td rowspan='{len(peers)}'>{backfill}</td>"
                        if i == 0 else "")
                out.append(f"<tr>{lead}<td>{e(p['id'])}</td><td>{me}</td>"
                           f"<td>{_switch(p['observe_others'])}</td></tr>")
        out.append("</table>")
        return "".join(out)
