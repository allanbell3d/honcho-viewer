"""GUI smoke tests: the real window, offscreen, talking HTTP to the fake server."""
import pytest
from PySide6.QtCore import Qt

from honcho_viewer.store import LocalStore
from tests.conftest import wait_for
from tests.fake_honcho import TOKEN


def _select(list_widget, value):
    for i in range(list_widget.count()):
        if list_widget.item(i).data(Qt.UserRole) == value:
            list_widget.setCurrentRow(i)
            return
    raise AssertionError(f"{value!r} not in list")


@pytest.fixture
def window(qapp, fake, tmp_path):
    from honcho_viewer.main_window import MainWindow

    store = LocalStore(tmp_path)
    store.set_connection(fake.url, TOKEN)
    store.set_label("test-qwen", "qwen3-32b")
    win = MainWindow(store)
    wait_for(lambda: win.workspace_list.count() == 3)
    yield win
    win.close()


@pytest.fixture
def on_alice(window):
    _select(window.workspace_list, "test-qwen")
    wait_for(lambda: window.peer_list.count() == 3)
    _select(window.peer_list, "alice")
    wait_for(lambda: window.peer_view.conclusions.table.rowCount() == 6)
    return window


def test_saved_token_auto_connects_and_shows_labels(window):
    assert "qwen3-32b" in window.workspace_list.item(0).text()
    assert "3 workspaces" in window.statusBar().currentMessage()


def test_rejected_token_explains_itself(qapp, fake, tmp_path):
    from honcho_viewer.main_window import MainWindow

    store = LocalStore(tmp_path)
    store.set_connection(fake.url, "wrong")
    win = MainWindow(store)
    wait_for(lambda: "401" in win.statusBar().currentMessage())
    assert "token" in win.statusBar().currentMessage().lower()
    win.close()


def test_empty_server_address_asks_for_it_instead_of_connecting(qapp, tmp_path):
    from honcho_viewer.main_window import MainWindow

    win = MainWindow(LocalStore(tmp_path))
    win.token_edit.setText("t")
    win.connect_btn.click()

    assert "server address" in win.statusBar().currentMessage()
    assert win.ctx.client is None and LocalStore(tmp_path).base_url == ""
    win.close()


def test_connect_button_saves_token(window, tmp_path):
    window.token_edit.setText("changed")
    window.connect_btn.click()

    assert LocalStore(tmp_path).token == "changed"


def test_label_edit_is_saved(window, tmp_path):
    _select(window.workspace_list, "test-llama")
    window.label_edit.setText("llama3-70b")
    window.label_edit.editingFinished.emit()

    assert LocalStore(tmp_path).label("test-llama") == "llama3-70b"
    assert "llama3-70b" in window.workspace_list.currentItem().text()


def test_peer_selection_loads_overview(on_alice):
    ov = on_alice.peer_view.overview
    wait_for(lambda: "qwen3-32b representation of alice" in ov.rep_view.toPlainText())
    wait_for(lambda: "alice lives in Lisbon" in ov.card_view.toPlainText())


def test_overview_says_the_representation_is_an_excerpt(on_alice):
    ov = on_alice.peer_view.overview
    wait_for(lambda: "of 6 conclusions" in ov.rep_note.text())
    assert "up to 100" in ov.rep_note.text()


def test_rating_saves_and_moves_to_next_row(on_alice, tmp_path):
    tab = on_alice.peer_view.conclusions
    tab.table.selectRow(0)
    first = tab.current_conclusion()

    tab.rate("correct")

    assert LocalStore(tmp_path).rating("test-qwen", first["id"]) == "correct"
    assert tab.table.currentRow() == 1
    assert "100%" in tab.summary_label.text()


def test_conclusions_default_to_the_peers_own_view(on_alice, fake):
    tab = on_alice.peer_view.conclusions
    assert tab.observer_combo.currentData() == "alice" and tab.about_combo.currentData() == "alice"
    assert {c["observer_id"] for c in tab.rows} == {"alice"}
    assert fake.last("conclusions/list")["body"]["filters"] == {"observer_id": "alice", "observed_id": "alice"}
    assert len({c["content"] for c in tab.rows}) == len(tab.rows)  # no duplicated facts


def test_conclusions_anyone_shows_the_duplicates(on_alice):
    tab = on_alice.peer_view.conclusions
    tab.observer_combo.setCurrentIndex(tab.observer_combo.findData(None))

    wait_for(lambda: tab.table.rowCount() == 13)  # 6 own + 6 agent copies + 1 agent-only
    assert tab.observer_combo.currentText() == "(anyone)"
    assert "twice" in tab.observer_combo.toolTip()


def test_snapshot_ignores_other_observers_and_old_mixed_snapshots(on_alice, fake, tmp_path):
    tab = on_alice.peer_view.conclusions
    tab.snapshot_btn.click()
    wait_for(lambda: "6 conclusions" in tab.snapshot_label.text())
    saved = LocalStore(tmp_path).load_snapshot("test-qwen", "alice")["conclusions"]
    assert all(c["observer_id"] == "alice" for c in saved)
    # a 0.0.1-style snapshot holding every observer's rows must not report the copies as removed
    rows = [c for c in fake.workspaces["test-qwen"]["conclusions"] if c["observed_id"] == "alice"]
    LocalStore(tmp_path).save_snapshot("test-qwen", "alice", rows)

    tab.diff_btn.click()

    wait_for(lambda: "0 added" in tab.detail.toPlainText())
    assert "0 removed" in tab.detail.toPlainText()


def test_text_filter_hides_rows(on_alice):
    tab = on_alice.peer_view.conclusions
    tab.observer_combo.setCurrentIndex(tab.observer_combo.findData("agent"))  # default By is alice itself
    wait_for(lambda: tab.table.rowCount() == 7)
    tab.text_filter.setText("Agent thinks")

    assert tab.table.rowCount() == 1


def test_dream_conclusion_shows_its_premises(on_alice):
    tab = on_alice.peer_view.conclusions
    row = next(i for i in range(tab.table.rowCount())
               if tab.table.item(i, 0).data(Qt.UserRole)["level"] == "deductive")
    tab.table.selectRow(row)

    wait_for(lambda: "Alice fact 0" in tab.detail.toPlainText())
    assert "Built from" in tab.detail.toPlainText()


def test_supporting_messages_search(on_alice, fake):
    tab = on_alice.peer_view.conclusions
    tab.table.selectRow(0)
    tab.support_btn.click()

    wait_for(lambda: "I live in Lisbon" in tab.detail.toPlainText())


def test_snapshot_then_what_changed(on_alice, fake):
    tab = on_alice.peer_view.conclusions
    tab.snapshot_btn.click()
    wait_for(lambda: "Snapshot" in tab.snapshot_label.text() and "6 conclusions" in tab.snapshot_label.text())
    fake.workspaces["test-qwen"]["conclusions"].append(
        {"id": "dreamed", "content": "Alice is a weekend sailor", "observer_id": "alice",
         "observed_id": "alice", "session_id": None, "level": "inductive", "source_ids": ["test-qwen-c0"],
         "times_derived": 1, "created_at": "2026-10-06T10:00:00Z"})

    tab.diff_btn.click()

    wait_for(lambda: "1 added" in tab.detail.toPlainText())
    assert "weekend sailor" in tab.detail.toPlainText()


def test_messages_tab_loads_more_pages(on_alice):
    tab = on_alice.peer_view.messages
    wait_for(lambda: tab.session_list.count() == 2)
    _select(tab.session_list, "s2")
    wait_for(lambda: "filler message 49" in tab.view.toPlainText())

    tab.more_btn.click()

    wait_for(lambda: "filler message 99" in tab.view.toPlainText())


def test_ask_preview_and_answer(on_alice, fake):
    ask = on_alice.peer_view.ask
    ask.question.setPlainText("where does he live?")

    assert '"query": "where does he live?"' in ask.preview.toPlainText()
    ask.ask_btn.click()

    wait_for(lambda: "answer to: where does he live?" in ask.history.toPlainText())
    assert fake.last("/chat")["body"]["include_evidence"] is True
    assert "search_memory" in ask.history.toPlainText()


def test_ask_options_have_tooltips(on_alice):
    ask = on_alice.peer_view.ask
    for widget in (ask.question, ask.reasoning, ask.evidence, ask.about_combo, ask.session_combo,
                   ask.scope_edit, ask.format_edit, ask.preview):
        assert widget.toolTip(), widget


def test_compare_counts_views_and_ask_all(window, fake):
    cmp = window.compare
    cmp.set_checked(["test-qwen", "test-llama"])
    wait_for(lambda: cmp.peer_combo.findText("alice") >= 0)
    cmp.peer_combo.setCurrentText("alice")
    cmp.load_btn.click()

    wait_for(lambda: cmp.counts_table.columnCount() == 2 and cmp.cell("Conclusions", 1) == "6")
    assert cmp.cell("Model / label", 0) == "qwen3-32b"

    cmp.show_combo.setCurrentText("Representation")
    assert "llama3-70b representation of alice" in cmp.columns.text(1)

    cmp.question.setText("hello?")
    cmp.ask_btn.click()
    wait_for(lambda: "[llama3-70b] answer to: hello?" in cmp.columns.text(1)
             and "[qwen3-32b] answer to: hello?" in cmp.columns.text(0))


def test_compare_counts_are_not_doubled(window, fake):
    cmp = window.compare
    cmp.set_checked(["test-qwen"])
    wait_for(lambda: cmp.peer_combo.findText("alice") >= 0)
    cmp.peer_combo.setCurrentText("alice")
    cmp.load_btn.click()

    wait_for(lambda: cmp.cell("Conclusions", 0) == "6")
    assert fake.last("conclusions/list")["body"]["filters"] == {"observer_id": "alice", "observed_id": "alice"}


def test_compare_ask_all_without_loading_first_still_loads_counts(window):
    cmp = window.compare
    cmp.set_checked(["test-qwen", "test-llama"])
    wait_for(lambda: cmp.peer_combo.findText("alice") >= 0)
    cmp.peer_combo.setCurrentText("alice")
    cmp.question.setText("hi")
    cmp.ask_btn.click()

    wait_for(lambda: "answer to: hi" in cmp.columns.text(0) and "answer to: hi" in cmp.columns.text(1))
    cmp.show_combo.setCurrentText("Counts")
    wait_for(lambda: cmp.cell("Conclusions", 0) == "6" and cmp.cell("Conclusions", 1) == "6")


def test_compare_save_is_off_until_something_is_loaded(window):
    assert not window.compare.save_btn.isEnabled()
    assert window.compare.save_btn.toolTip()


def test_compare_save_results_writes_a_readable_dated_report(window, tmp_path):
    import json
    import re

    cmp = window.compare
    cmp.set_checked(["test-qwen", "test-llama"])
    wait_for(lambda: cmp.peer_combo.findText("alice") >= 0)
    cmp.peer_combo.setCurrentText("alice")
    cmp.question.setText("hello?")
    cmp.ask_btn.click()
    wait_for(lambda: "answer to: hello?" in cmp.columns.text(0) and "answer to: hello?" in cmp.columns.text(1))
    cmp.show_combo.setCurrentText("Counts")
    wait_for(lambda: cmp.cell("Conclusions", 0) == "6" and cmp.cell("Conclusions", 1) == "6")

    cmp.save_btn.click()

    files = list((tmp_path / "comparisons").glob("*.html"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    for expected in ("hello?", "[llama3-70b] answer to: hello?", "[qwen3-32b] answer to: hello?",
                     "test-qwen", "test-llama", "qwen3-32b", "llama3-70b representation of alice"):
        assert expected in text, expected
    data = json.loads(re.search(r'<script type="application/json" id="comparison-data">(.*?)</script>',
                                text, re.S).group(1))
    assert data["peer"] == "alice" and data["question"] == "hello?" and data["reasoning"] == "low"
    assert data["workspaces"]["test-qwen"]["counts"]["Conclusions"] == "6"
    assert data["workspaces"]["test-llama"]["answer"]["content"] == "[llama3-70b] answer to: hello?"
    assert len(data["workspaces"]["test-qwen"]["conclusions"]) == 6


def test_compare_two_saves_never_overwrite_each_other(window, tmp_path):
    cmp = window.compare
    cmp.set_checked(["test-qwen"])
    wait_for(lambda: cmp.peer_combo.findText("alice") >= 0)
    cmp.peer_combo.setCurrentText("alice")
    cmp.load_btn.click()
    wait_for(lambda: cmp.cell("Conclusions", 0) == "6")

    cmp.save_btn.click()
    cmp.save_btn.click()

    assert len(list((tmp_path / "comparisons").glob("*.html"))) == 2


def _open_peers(window, ws="test-qwen"):
    cp = window.compare_peers
    cp.ws_combo.setCurrentIndex(cp.ws_combo.findData(ws))
    wait_for(lambda: cp.peer_list.count() == 3 and cp.load_btn.isEnabled())
    return cp


def test_compare_peers_lists_every_peer_ticked_including_the_user(window):
    cp = _open_peers(window)

    assert len(cp.checked()) == 3 and "alice" in cp.checked()


def test_compare_peers_ask_all_answers_side_by_side(window):
    cp = _open_peers(window)
    cp.question.setText("what do you know about me?")
    cp.ask_btn.click()

    wait_for(lambda: all("answer to: what do you know about me?" in cp.columns.text(i) for i in range(3)))
    cp.show_combo.setCurrentText("Counts")
    wait_for(lambda: cp.counts_table.columnCount() == 3 and cp.cell("Conclusions", 0) != "…")
    assert [cp.cell("Peer", i) for i in range(3)] == cp.checked()


def test_compare_peers_none_then_one_peer(window):
    cp = _open_peers(window)
    cp.none_btn.click()
    assert not cp.load_btn.isEnabled()

    cp.set_checked(["alice"])
    cp.load_btn.click()
    wait_for(lambda: cp.counts_table.columnCount() == 1 and cp.cell("Conclusions", 0) == "6")


def test_compare_peers_save_writes_a_report_with_every_peer(window, tmp_path):
    import json
    import re

    cp = _open_peers(window)
    cp.question.setText("hello?")
    cp.ask_btn.click()
    wait_for(lambda: all("answer to: hello?" in cp.columns.text(i) for i in range(3)))
    cp.show_combo.setCurrentText("Counts")
    wait_for(lambda: all(cp.cell("Conclusions", i) not in ("", "…") for i in range(3)))

    cp.save_btn.click()

    text = next((tmp_path / "comparisons").glob("*.html")).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script type="application/json" id="comparison-data">(.*?)</script>',
                                text, re.S).group(1))
    assert data["workspace"] == "test-qwen" and sorted(data["peers"]) == sorted(cp.checked())
    assert data["peers"]["alice"]["answer"]["content"].endswith("answer to: hello?")


def test_viewer_never_calls_unknown_routes(on_alice, fake):
    assert fake.unexpected == []
