# Contributing

Issues and pull requests are welcome.

- Python 3.10+, PySide6. `pip install -r requirements.txt pytest`, then `python -m pytest` (offline, a few seconds).
- **Keep it read-only.** New server calls must be reads and go through the allowlist in `src/honcho_viewer/client.py`;
  a test fails if the app calls anything else.
- **New compare page?** Subclass `ComparePanel` (`src/honcho_viewer/compare_panel.py`) and provide the selector plus
  the few hook methods. Don't copy the loading, asking or saving code.
- Add a test for behaviour changes. The test suite runs the real window against `tests/fake_honcho.py`.
- Never commit anything from `local/`, and keep real server addresses, tokens and memory content out of
  tests, fixtures and screenshots. Use made-up data.
- Add a line to `CHANGELOG.md`; the version lives only in `src/honcho_viewer/__init__.py`.

## Branches and releases

- `main` is the latest stable release; every release is tagged (`v0.1.1`, ...).
- Work happens on `dev` (or a feature branch off it). At each release `dev` is merged into `main`, the version in
  `src/honcho_viewer/__init__.py` is bumped, `CHANGELOG.md` is updated and the merge is tagged.
- Open pull requests against `dev`.
