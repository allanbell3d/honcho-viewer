import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import pytest

from tests.fake_honcho import TOKEN, FakeHoncho


@pytest.fixture
def fake():
    server = FakeHoncho()
    server.url = server.start()
    yield server
    server.stop()


@pytest.fixture
def client(fake):
    from honcho_viewer.client import HonchoClient

    return HonchoClient(fake.url, TOKEN)


@pytest.fixture(autouse=True)
def no_native_dialogs(monkeypatch):
    """File dialogs block forever offscreen. Save accepts the suggested path, Open cancels; tests override."""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda parent=None, caption="", directory="", filter="": (directory, "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def wait_for(predicate, timeout: float = 5.0) -> None:
    """Pump the Qt event loop until ``predicate()`` is truthy."""
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")
