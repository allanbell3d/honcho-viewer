import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .store import LocalStore

SOURCE_ROOT = Path(__file__).resolve().parents[2]  # the checkout: <root>/src/honcho_viewer/app.py


def data_dir() -> Path:
    """Where settings, token, ratings and snapshots live (always a folder git ignores or is outside the repo)."""
    override = os.environ.get("HONCHO_VIEWER_HOME")
    if override:
        return Path(override)
    if (SOURCE_ROOT / "pyproject.toml").exists():  # running from a git checkout: keep data next to the app
        return SOURCE_ROOT / "local"
    return Path.home() / ".honcho-viewer"  # pip-installed: the package folder is not a place for user data


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Honcho Viewer")
    window = MainWindow(LocalStore(data_dir()))
    window.show()
    return app.exec()
