import json

from honcho_viewer.store import LocalStore, diff_conclusions


def _c(cid, level="explicit", observed="alice", content="x"):
    return {"id": cid, "level": level, "observer_id": "alice", "observed_id": observed, "content": content}


def test_defaults_when_nothing_saved(tmp_path):
    store = LocalStore(tmp_path)

    assert store.base_url == ""
    assert store.token == ""
    assert not (tmp_path / "settings.json").exists()


def test_connection_survives_reopen(tmp_path):
    LocalStore(tmp_path).set_connection("http://h:8000", "secret")

    reopened = LocalStore(tmp_path)
    assert (reopened.base_url, reopened.token) == ("http://h:8000", "secret")


def test_corrupt_settings_are_kept_aside_not_destroyed(tmp_path):
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")

    store = LocalStore(tmp_path)

    assert store.token == ""
    assert (tmp_path / "settings.json.bad").read_text(encoding="utf-8") == "{not json"


def test_workspace_labels_set_and_clear(tmp_path):
    store = LocalStore(tmp_path)
    store.set_label("test-qwen", " qwen3-32b ")
    assert LocalStore(tmp_path).label("test-qwen") == "qwen3-32b"

    store.set_label("test-qwen", "")
    assert LocalStore(tmp_path).label("test-qwen") == ""


def test_ui_values_round_trip(tmp_path):
    LocalStore(tmp_path).set_ui_value("geometry", "abc")

    assert LocalStore(tmp_path).ui_value("geometry") == "abc"
    assert LocalStore(tmp_path).ui_value("missing", 5) == 5


def test_rating_persists_with_conclusion_text(tmp_path):
    LocalStore(tmp_path).set_rating("ws", _c("c1", content="likes sailing"), "correct")

    store = LocalStore(tmp_path)
    assert store.rating("ws", "c1") == "correct"
    saved = json.loads((tmp_path / "ratings.json").read_text(encoding="utf-8"))
    assert saved["ws"]["c1"]["content"] == "likes sailing"


def test_clearing_a_rating(tmp_path):
    store = LocalStore(tmp_path)
    store.set_rating("ws", _c("c1"), "wrong")
    store.set_rating("ws", _c("c1"), None)

    assert LocalStore(tmp_path).rating("ws", "c1") is None


def test_accuracy_ignores_unsure(tmp_path):
    store = LocalStore(tmp_path)
    for cid, r in [("a", "correct"), ("b", "correct"), ("c", "correct"), ("d", "wrong"), ("e", "unsure")]:
        store.set_rating("ws", _c(cid), r)

    acc = store.accuracy("ws")
    assert (acc.correct, acc.wrong, acc.unsure, acc.rated) == (3, 1, 1, 5)
    assert acc.percent == 75


def test_accuracy_without_verdicts_is_none(tmp_path):
    store = LocalStore(tmp_path)
    store.set_rating("ws", _c("a"), "unsure")

    assert store.accuracy("ws").percent is None
    assert store.accuracy("other").rated == 0


def test_accuracy_can_be_limited_to_ids_or_observed_peer(tmp_path):
    store = LocalStore(tmp_path)
    store.set_rating("ws", _c("a"), "correct")
    store.set_rating("ws", _c("b", observed="agent"), "wrong")

    assert store.accuracy("ws", ids={"b"}).wrong == 1
    assert store.accuracy("ws", observed_id="alice").rated == 1


def test_snapshot_round_trip_with_awkward_ids(tmp_path):
    store = LocalStore(tmp_path)
    taken = store.save_snapshot("ws", "user@home/x", [_c("c1")])

    snap = LocalStore(tmp_path).load_snapshot("ws", "user@home/x")
    assert snap["taken_at"] == taken
    assert [c["id"] for c in snap["conclusions"]] == ["c1"]
    assert store.load_snapshot("ws", "nobody") is None


def test_diff_conclusions_reports_added_and_removed():
    before = [_c("a"), _c("b")]
    after = [_c("b"), _c("c", level="deductive"), _c("d", level="inductive")]

    diff = diff_conclusions(before, after)

    assert [c["id"] for c in diff["added"]] == ["c", "d"]
    assert [c["id"] for c in diff["removed"]] == ["a"]
    assert diff["kept"] == 1


def test_data_dir_is_the_checkouts_local_folder_not_src(monkeypatch):
    from honcho_viewer.app import SOURCE_ROOT, data_dir

    monkeypatch.delenv("HONCHO_VIEWER_HOME", raising=False)
    assert data_dir() == SOURCE_ROOT / "local"
    assert (SOURCE_ROOT / "pyproject.toml").exists() and SOURCE_ROOT.name != "src"


def test_data_dir_can_be_overridden(monkeypatch, tmp_path):
    from honcho_viewer.app import data_dir

    monkeypatch.setenv("HONCHO_VIEWER_HOME", str(tmp_path))
    assert data_dir() == tmp_path


def test_snapshots_are_dated_files_and_never_overwrite_each_other(tmp_path):
    store = LocalStore(tmp_path)
    store.save_snapshot("ws", "p", [_c("a")])
    store.save_snapshot("ws", "p", [_c("a"), _c("b")])

    files = sorted((tmp_path / "snapshots").glob("*.json"))
    assert len(files) == 2
    assert [c["id"] for c in store.load_snapshot("ws", "p")["conclusions"]] == ["a", "b"]  # newest wins


def test_load_snapshot_still_finds_the_old_one_file_per_peer_format(tmp_path):
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "ws__p.json").write_text(
        '{"taken_at": "2026-10-01T10:00:00", "conclusions": [{"id": "old"}]}', encoding="utf-8")

    assert LocalStore(tmp_path).load_snapshot("ws", "p")["conclusions"][0]["id"] == "old"
    assert LocalStore(tmp_path).load_snapshot("ws", "someone-else") is None


def test_snapshot_of_a_peer_whose_name_continues_another_is_not_mixed_up(tmp_path):
    store = LocalStore(tmp_path)
    store.save_snapshot("ws", "x", [_c("short")])
    store.save_snapshot("ws", "x__y", [_c("long")])

    assert store.load_snapshot("ws", "x")["conclusions"][0]["id"] == "short"
    assert store.load_snapshot("ws", "x__y")["conclusions"][0]["id"] == "long"


def test_read_snapshot_rejects_files_that_are_not_snapshots(tmp_path):
    junk = tmp_path / "junk.json"
    junk.write_text('{"hello": 1}', encoding="utf-8")
    assert LocalStore.read_snapshot(junk) is None
    assert LocalStore.read_snapshot(tmp_path / "missing.json") is None
