"""The telemetry listener controls, shared by every tab that needs them (one piece of code, identical everywhere)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget

from .telemetry_hub import DEFAULT_PORT
from .widgets import AppContext, tip


class TelemetryPanel(QWidget):
    """Start/stop the listener, choose its port and optional limits, and copy the matching Honcho settings."""

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.port_spin = tip(QSpinBox(), "Port the listener waits on. Honcho's TELEMETRY_ENDPOINT must reach it.")
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(ctx.store.ui_value("telemetry_port", DEFAULT_PORT)))
        self.key_edit = tip(QLineEdit(ctx.store.ui_value("telemetry_key", ""), placeholderText="optional"),
                            "Optional shared secret. If set, Honcho must send it in the X-Telemetry-Key header, "
                            "otherwise the post is rejected.")
        self.allowed_edit = tip(QLineEdit(ctx.store.ui_value("telemetry_allowed", ""),
                                          placeholderText="anyone (or e.g. 192.168.1.22)"),
                                "Only accept posts from these sender IP addresses (comma separated), e.g. your "
                                "Honcho server. Empty = anyone who can reach the port.")
        self.listen_btn = tip(QPushButton("Start listening"),
                              "Starts a small listener on this PC that receives Honcho's telemetry. It only "
                              "receives; it never talks back to Honcho.")
        self.listen_btn.clicked.connect(self.toggle)
        self.copy_btn = tip(QPushButton("Copy Honcho settings"),
                            "Copies the TELEMETRY_* lines to paste into Honcho's environment.")
        self.copy_btn.clicked.connect(self.copy_settings)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status_label)
        for label, widget in (("Port", self.port_spin), ("Only accept from", self.allowed_edit),
                              ("Secret", self.key_edit)):
            layout.addWidget(QLabel(label))
            layout.addWidget(widget)
        layout.addWidget(self.listen_btn)
        layout.addWidget(self.copy_btn)
        ctx.telemetry.state_changed.connect(self.update_state)
        ctx.telemetry.events_received.connect(self.update_state)
        self.update_state()

    def toggle(self) -> None:
        hub = self.ctx.telemetry
        if hub.running:
            hub.stop()
            return
        try:
            hub.start(self.port_spin.value(), self.key_edit.text(), self.allowed_edit.text())
        except OSError as exc:
            self.ctx.status.emit(f"Cannot listen on port {self.port_spin.value()}: {exc}. Try another port.")

    def settings_text(self) -> str:
        hub = self.ctx.telemetry
        lines = ["TELEMETRY_ENABLED=true", f"TELEMETRY_ENDPOINT={hub.url()}"]
        if hub.key:
            lines.append('TELEMETRY_HEADERS={"X-Telemetry-Key": "' + hub.key + '"}')
        return "\n".join(lines)

    def copy_settings(self) -> None:
        QGuiApplication.clipboard().setText(self.settings_text())
        self.ctx.status.emit("Copied. Paste these into Honcho's environment and restart Honcho.")

    def update_state(self, _count: int = 0) -> None:
        hub = self.ctx.telemetry
        self.listen_btn.setText("Stop listening" if hub.running else "Start listening")
        for widget in (self.port_spin, self.key_edit, self.allowed_edit):
            widget.setEnabled(not hub.running)
        self.copy_btn.setEnabled(hub.running)
        if hub.running:
            note = f" Cannot write the log: {hub.receiver.last_error}" if hub.receiver.last_error else ""
            self.status_label.setText(f"Listening on {hub.url()}<br>{hub.store.total} events received. Everything "
                                      f"is logged in local/telemetry/.{note}")
        else:
            self.status_label.setText("Not listening. Start it, then point Honcho's TELEMETRY_ENDPOINT here to "
                                      "see live numbers.")
