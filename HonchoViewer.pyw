"""Double-click to launch the Honcho Viewer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from honcho_viewer.app import main  # noqa: E402

raise SystemExit(main())
