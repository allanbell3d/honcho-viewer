"""Qt wrapper around the telemetry receiver: one per app, owned by AppContext.ctx.telemetry."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from .history import HistoryState
from .monitor import MonitorState
from .store import LocalStore
from .telemetry import EventLog, TelemetryReceiver, TelemetryStore, lan_address, parse_allowed

DEFAULT_PORT = 8099


class TelemetryHub(QObject):
    events_received = Signal(int)  # emitted on the GUI thread (queued) whenever events arrive
    state_changed = Signal()

    def __init__(self, store: LocalStore):
        super().__init__()
        self._store = store
        self.store = TelemetryStore()
        cap_mb = store.ui_value("telemetry_cap_mb", 1024)
        self.log = EventLog(store.root / "telemetry", int(cap_mb) * 1024 * 1024)
        self.monitor = MonitorState(stall_after=int(store.ui_value("monitor_stall_min", 3)) * 60)
        self.history = HistoryState()
        self.receiver = TelemetryReceiver(self.store, self.log, on_events=self.events_received.emit,
                                          on_batch=self._batch)

    def _batch(self, events: list[dict], received_at: float) -> None:
        """Runs on the receiver's thread: both consumers are thread-safe."""
        self.monitor.ingest(events, received_at)
        self.history.add_events(events, received_at)

    @property
    def running(self) -> bool:
        return self.receiver.running

    @property
    def port(self) -> int:
        return self.receiver.port or int(self._store.ui_value("telemetry_port", DEFAULT_PORT))

    @property
    def key(self) -> str:
        return self._store.ui_value("telemetry_key", "")

    @property
    def allowed(self) -> str:
        return self._store.ui_value("telemetry_allowed", "")

    def url(self) -> str:
        return f"http://{lan_address()}:{self.port}/"

    def start(self, port: int, key: str, allowed: str = "") -> None:
        self._store.set_ui_value("telemetry_port", int(port))
        self._store.set_ui_value("telemetry_key", key.strip())
        self._store.set_ui_value("telemetry_allowed", allowed.strip())
        self.receiver.key = key.strip()
        self.receiver.allowed = parse_allowed(allowed)
        self.receiver.start(int(port))  # raises OSError if the port is taken
        self.state_changed.emit()

    def stop(self) -> None:
        self.receiver.stop()
        self.state_changed.emit()
