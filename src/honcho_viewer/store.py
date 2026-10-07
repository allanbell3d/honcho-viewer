"""Everything the viewer keeps on this PC, under one folder (``local/``, git-ignored).

settings.json   server URL, token, workspace labels, window state
ratings.json    your correct/wrong/unsure verdicts on conclusions
snapshots/      one saved conclusion list per workspace+peer, for before/after diffs
comparisons/    Compare-tab results saved with "Save results" (one dated HTML report each)
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

RATINGS = ("correct", "wrong", "unsure")
_SETTINGS_DEFAULTS = {"base_url": "", "token": "", "workspace_labels": {}, "ui": {}}


@dataclass(frozen=True)
class Accuracy:
    correct: int = 0
    wrong: int = 0
    unsure: int = 0

    @property
    def rated(self) -> int:
        return self.correct + self.wrong + self.unsure

    @property
    def percent(self) -> int | None:
        """Share of correct among definite verdicts; 'unsure' doesn't count either way."""
        decided = self.correct + self.wrong
        return round(100 * self.correct / decided) if decided else None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    if not path.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        path.replace(path.with_name(path.name + ".bad"))  # keep it for inspection
        return copy.deepcopy(default)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written file


class LocalStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._settings_path = self.root / "settings.json"
        self._ratings_path = self.root / "ratings.json"
        self._settings = {**copy.deepcopy(_SETTINGS_DEFAULTS), **_read_json(self._settings_path, {})}
        self._ratings: dict[str, dict[str, dict]] = _read_json(self._ratings_path, {})

    # ---- connection & labels -------------------------------------------
    @property
    def base_url(self) -> str:
        return self._settings["base_url"]

    @property
    def token(self) -> str:
        return self._settings["token"]

    def set_connection(self, base_url: str, token: str) -> None:
        self._settings.update(base_url=base_url.strip(), token=token.strip())
        _write_json(self._settings_path, self._settings)

    def label(self, ws: str) -> str:
        return self._settings["workspace_labels"].get(ws, "")

    def set_label(self, ws: str, label: str) -> None:
        labels = self._settings["workspace_labels"]
        if label.strip():
            labels[ws] = label.strip()
        else:
            labels.pop(ws, None)
        _write_json(self._settings_path, self._settings)

    def ui_value(self, key: str, default=None):
        return self._settings["ui"].get(key, default)

    def set_ui_value(self, key: str, value) -> None:
        self._settings["ui"][key] = value
        _write_json(self._settings_path, self._settings)

    # ---- ratings -------------------------------------------------------
    def rating(self, ws: str, conclusion_id: str) -> str | None:
        return self._ratings.get(ws, {}).get(conclusion_id, {}).get("rating")

    def set_rating(self, ws: str, conclusion: dict, rating: str | None) -> None:
        bucket = self._ratings.setdefault(ws, {})
        if rating is None:
            bucket.pop(conclusion["id"], None)
        else:
            if rating not in RATINGS:
                raise ValueError(f"rating must be one of {RATINGS}")
            bucket[conclusion["id"]] = {
                "rating": rating,
                "rated_at": _now(),
                **{k: conclusion.get(k) for k in ("content", "level", "observer_id", "observed_id")},
            }
        _write_json(self._ratings_path, self._ratings)

    def accuracy(self, ws: str, ids: Iterable[str] | None = None, observed_id: str | None = None) -> Accuracy:
        wanted = set(ids) if ids is not None else None
        counts = dict.fromkeys(RATINGS, 0)
        for cid, entry in self._ratings.get(ws, {}).items():
            if wanted is not None and cid not in wanted:
                continue
            if observed_id is not None and entry.get("observed_id") != observed_id:
                continue
            counts[entry["rating"]] += 1
        return Accuracy(**counts)

    # ---- snapshots -----------------------------------------------------
    def _snapshot_path(self, ws: str, peer: str) -> Path:
        return self.root / "snapshots" / f"{quote(ws, safe='')}__{quote(peer, safe='')}.json"

    def save_snapshot(self, ws: str, peer: str, conclusions: list[dict]) -> str:
        taken_at = _now()
        _write_json(self._snapshot_path(ws, peer), {"taken_at": taken_at, "conclusions": conclusions})
        return taken_at

    def load_snapshot(self, ws: str, peer: str) -> dict | None:
        path = self._snapshot_path(ws, peer)
        return _read_json(path, None) if path.exists() else None

    # ---- saved comparisons ----------------------------------------------
    def save_comparison(self, html: str, peer: str) -> Path:
        """Write a Compare-tab report to comparisons/<date-time>_<peer>.html; never overwrites an earlier one."""
        folder = self.root / "comparisons"
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.now():%Y%m%d-%H%M%S}_{quote(peer, safe='')}"
        path, n = folder / f"{stem}.html", 2
        while path.exists():
            path, n = folder / f"{stem}-{n}.html", n + 1
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(html, encoding="utf-8")
        os.replace(tmp, path)
        return path


def diff_conclusions(before: list[dict], after: list[dict]) -> dict:
    """What changed between two conclusion lists (e.g. before and after a dream)."""
    old_ids = {c["id"] for c in before}
    new_ids = {c["id"] for c in after}
    return {
        "added": [c for c in after if c["id"] not in old_ids],
        "removed": [c for c in before if c["id"] not in new_ids],
        "kept": len(old_ids & new_ids),
    }
