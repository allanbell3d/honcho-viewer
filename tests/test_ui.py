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


def _open_reasoning(window, ws="test-qwen", peer="alice"):
    cr = window.compare_reasoning
    cr.ws_combo.setCurrentIndex(cr.ws_combo.findData(ws))
    wait_for(lambda: cr.peer_combo.findText(peer) >= 0)
    cr.peer_combo.setCurrentText(peer)
    return cr


def _post_dialectic(port, level, tokens, run=None):
    import json
    import urllib.request

    event = {"type": "dialectic.completed", "time": "2026-10-08T00:00:00Z", "data": {
        "run_id": run or f"run-{level}", "workspace_name": "test-qwen", "peer_name": "alice",
        "reasoning_level": level, "input_tokens": tokens, "output_tokens": tokens // 10,
        "cache_read_tokens": tokens // 2, "total_duration_ms": 4200.0, "total_iterations": 3,
        "tool_calls_count": 7, "prefetched_conclusion_count": 5}}
    child = {"type": "llm.call.completed", "time": "t", "data": {"run_id": event["data"]["run_id"],
                                                                  "model": "haiku-test"}}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=json.dumps([child, event]).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=5).read()


LEVELS = ["minimal", "low", "medium", "high", "max"]


def test_reasoning_tab_asks_each_level_in_order_with_streaming_and_time_stats(window, fake):
    cr = _open_reasoning(window)
    cr.question.setText("what do you know about me?")
    cr.ask_btn.click()
    cr.show_combo.setCurrentText("Stats")

    wait_for(lambda: cr.cell("Answer words", 4) not in ("", "…"))
    chats = [r["body"] for r in fake.requests if r["path"].endswith("/chat")]
    assert [b["reasoning_level"] for b in chats] == LEVELS  # one after another, lowest first
    assert all(b["stream"] is True and b["include_evidence"] is True for b in chats)
    assert [cr.cell("Reasoning level", i) for i in range(5)] == LEVELS
    assert float(cr.cell("Wall time (s)", 0)) > 0 and float(cr.cell("First words after (s)", 0)) >= 0
    assert cr.cell("Tool calls", 0) == "1" and cr.cell("Conclusions read", 0) == "2"
    assert cr.cell("Input tokens", 0) == "listener off"  # honest about what is missing
    cr.show_combo.setCurrentText("Answers")
    assert "answer to: what do you know about me?" in cr.columns.text(2)


def test_reasoning_tab_only_asks_the_ticked_levels(window, fake):
    cr = _open_reasoning(window)
    cr.set_levels(["low", "high"])
    cr.question.setText("hi")
    cr.ask_btn.click()
    cr.show_combo.setCurrentText("Stats")

    wait_for(lambda: cr.cell("Answer words", 1) not in ("", "…"))
    assert [r["body"]["reasoning_level"] for r in fake.requests if r["path"].endswith("/chat")] == ["low", "high"]


def test_reasoning_tab_fills_token_stats_from_live_telemetry(window):
    cr = _open_reasoning(window)
    window.ctx.telemetry.start(0, "")
    port = window.ctx.telemetry.port
    cr.set_levels(["low", "high"])
    cr.question.setText("hi")
    cr.ask_btn.click()
    cr.show_combo.setCurrentText("Stats")
    wait_for(lambda: cr.cell("Answer words", 1) not in ("", "…"))
    assert cr.cell("Input tokens", 0) == "waiting…"

    _post_dialectic(port, "high", 90000)
    _post_dialectic(port, "low", 9000)

    wait_for(lambda: cr.cell("Input tokens", 0) == "9,000" and cr.cell("Input tokens", 1) == "90,000")
    assert (cr.cell("Output tokens", 1), cr.cell("  of which cached", 1)) == ("9,000", "45,000")
    assert cr.cell("Server time (s)", 0) == "4.2" and cr.cell("Iterations", 0) == "3"
    assert cr.cell("Tool calls", 0) == "7" and cr.cell("Model(s)", 0) == "haiku-test"
    window.ctx.telemetry.stop()


def test_reasoning_tab_does_not_match_another_levels_event(window):
    cr = _open_reasoning(window)
    window.ctx.telemetry.start(0, "")
    cr.set_levels(["low"])
    cr.question.setText("hi")
    cr.ask_btn.click()
    cr.show_combo.setCurrentText("Stats")
    wait_for(lambda: cr.cell("Answer words", 0) not in ("", "…"))

    _post_dialectic(window.ctx.telemetry.port, "max", 5000)  # somebody else's call at another level

    wait_for(lambda: window.ctx.telemetry.store.total == 2)
    assert cr.cell("Input tokens", 0) == "waiting…"
    window.ctx.telemetry.stop()


def test_reasoning_save_writes_stats_and_telemetry(window, tmp_path):
    import json
    import re

    cr = _open_reasoning(window)
    window.ctx.telemetry.start(0, "")
    cr.set_levels(["low"])
    cr.question.setText("hi")
    cr.ask_btn.click()
    cr.show_combo.setCurrentText("Stats")
    wait_for(lambda: cr.cell("Answer words", 0) not in ("", "…"))
    _post_dialectic(window.ctx.telemetry.port, "low", 1234)
    wait_for(lambda: cr.cell("Input tokens", 0) == "1,234")

    cr.save_btn.click()

    text = next((tmp_path / "comparisons").glob("*.html")).read_text(encoding="utf-8")
    data = json.loads(re.search(r'<script type="application/json" id="comparison-data">(.*?)</script>',
                                text, re.S).group(1))
    col = data["levels"]["low"]
    assert data["peer"] == "alice" and col["telemetry"]["input_tokens"] == 1234
    assert col["counts"]["Input tokens"] == "1,234" and "card" not in col
    assert any(e["type"] == "llm.call.completed" for e in col["run_events"])
    window.ctx.telemetry.stop()


def test_reasoning_tab_listener_buttons_and_copyable_settings(window):
    cr = window.compare_reasoning
    assert not window.ctx.telemetry.running and not cr.telemetry_panel.copy_btn.isEnabled()

    window.ctx.telemetry.start(0, "k3y")

    assert cr.telemetry_panel.listen_btn.text() == "Stop listening" and cr.telemetry_panel.copy_btn.isEnabled()
    text = cr.telemetry_panel.settings_text()
    assert "TELEMETRY_ENABLED=true" in text and "TELEMETRY_ENDPOINT=http://" in text and "k3y" in text
    window.ctx.telemetry.stop()
    assert cr.telemetry_panel.listen_btn.text() == "Start listening"


def _saved_pages(tmp_path):
    return sorted((tmp_path / "comparisons").glob("*.html"))


def _load_models_tab(window):
    cmp = window.compare
    cmp.set_checked(["test-qwen"])
    wait_for(lambda: cmp.peer_combo.findText("alice") >= 0)
    cmp.peer_combo.setCurrentText("alice")
    cmp.load_btn.click()
    wait_for(lambda: cmp.save_btn.isEnabled())
    return cmp


def _open(tab, path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    tab.open_btn.click()


def test_save_results_asks_where_and_remembers_the_folder(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    cmp = _load_models_tab(window)
    chosen = tmp_path / "elsewhere" / "my-run.html"
    asked = []

    def fake_dialog(parent, caption, directory, filter=""):
        asked.append(directory.replace("\\", "/"))
        return str(chosen), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_dialog))

    cmp.save_btn.click()

    assert chosen.exists() and not _saved_pages(tmp_path)  # went where I chose, not to the default folder
    assert asked[0].endswith(".html") and "comparisons" in asked[0]
    cmp.save_btn.click()  # next time the dialog starts in the folder I used
    assert asked[1].startswith(str(chosen.parent).replace("\\", "/"))


def test_save_results_cancel_writes_nothing(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    cmp = _load_models_tab(window)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))

    cmp.save_btn.click()

    assert not _saved_pages(tmp_path) and "Not saved" in window.statusBar().currentMessage()


def test_open_saved_compare_models_restores_counts_answers_and_views(window, tmp_path, monkeypatch):
    from honcho_viewer.main_window import MainWindow

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
    page = _saved_pages(tmp_path)[0]

    fresh = MainWindow(LocalStore(tmp_path))  # a new session: nothing loaded yet
    wait_for(lambda: fresh.workspace_list.count() == 3)
    _open(fresh.compare, page, monkeypatch)

    again = fresh.compare
    assert again.counts_table.columnCount() == 2 and again.cell("Conclusions", 1) == "6"
    assert again.cell("Model / label", 0) == "qwen3-32b" and not again.save_btn.isEnabled()
    again.show_combo.setCurrentText("Answers")
    assert "[llama3-70b] answer to: hello?" in again.columns.text(1)
    again.show_combo.setCurrentText("Representation")
    assert "llama3-70b representation of alice" in again.columns.text(1)
    message = fresh.statusBar().currentMessage()
    assert "Opened" in message and "hello?" in message
    fresh.close()


def test_open_saved_reasoning_restores_stats_and_telemetry(window, tmp_path, monkeypatch):
    from honcho_viewer.main_window import MainWindow

    cr = _open_reasoning(window)
    window.ctx.telemetry.start(0, "")
    cr.set_levels(["low", "high"])
    cr.question.setText("hi")
    cr.ask_btn.click()
    cr.show_combo.setCurrentText("Stats")
    wait_for(lambda: cr.cell("Answer words", 1) not in ("", "…"))
    _post_dialectic(window.ctx.telemetry.port, "low", 9000)
    _post_dialectic(window.ctx.telemetry.port, "high", 90000)
    wait_for(lambda: cr.cell("Input tokens", 1) == "90,000")
    cr.save_btn.click()
    window.ctx.telemetry.stop()
    page = _saved_pages(tmp_path)[0]

    fresh = MainWindow(LocalStore(tmp_path))
    wait_for(lambda: fresh.workspace_list.count() == 3)
    _open(fresh.compare_reasoning, page, monkeypatch)

    again = fresh.compare_reasoning
    assert [again.cell("Reasoning level", i) for i in range(2)] == ["low", "high"]
    assert again.cell("Input tokens", 0) == "9,000" and again.cell("Input tokens", 1) == "90,000"
    assert again.cell("Server time (s)", 1) == "4.2" and again.cell("Model(s)", 0) == "haiku-test"
    assert again.stats["high"]["telemetry"]["input_tokens"] == 90000 and again.stats["high"]["run_events"]
    again.show_combo.setCurrentText("Answers")
    assert "answer to: hi" in again.columns.text(0)
    fresh.close()


def test_open_saved_rejects_the_wrong_kind_and_junk(window, tmp_path, monkeypatch):
    cmp = _load_models_tab(window)
    cmp.save_btn.click()
    models_page = _saved_pages(tmp_path)[0]

    _open(window.compare_peers, models_page, monkeypatch)
    assert "Compare models" in window.statusBar().currentMessage()

    junk = tmp_path / "junk.html"
    junk.write_text("<html>nothing</html>", encoding="utf-8")
    _open(cmp, junk, monkeypatch)
    assert "not a page saved by Honcho Viewer" in window.statusBar().currentMessage()


def test_asking_after_opening_goes_back_to_live_results(window, tmp_path, monkeypatch):
    cmp = _load_models_tab(window)
    cmp.save_btn.click()
    _open(cmp, _saved_pages(tmp_path)[0], monkeypatch)
    assert not cmp.save_btn.isEnabled()

    cmp.question.setText("again?")
    cmp.ask_btn.click()

    wait_for(lambda: "answer to: again?" in cmp.columns.text(0))
    assert cmp.save_btn.isEnabled() and cmp._opened is None


def test_snapshot_asks_where_to_save_and_cancel_saves_nothing(on_alice, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    tab = on_alice.peer_view.conclusions
    target = tmp_path / "mine" / "before-dream.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    tab.snapshot_btn.click()
    wait_for(lambda: target.exists())
    assert not list((tmp_path / "snapshots").glob("*.json"))  # went where I chose

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    tab.snapshot_btn.click()
    wait_for(lambda: "Snapshot not saved" in on_alice.statusBar().currentMessage())
    assert len(list((tmp_path / "mine").glob("*.json"))) == 1


def test_load_snapshot_sets_the_before_for_what_changed(on_alice, tmp_path, monkeypatch):
    import json

    from PySide6.QtWidgets import QFileDialog

    tab = on_alice.peer_view.conclusions
    older = tmp_path / "older.json"
    older.write_text(json.dumps({
        "workspace": "test-qwen", "peer": "alice", "taken_at": "2026-09-01T10:00:00",
        "conclusions": [{"id": "gone", "content": "an old conclusion", "observer_id": "alice",
                         "observed_id": "alice", "level": "explicit"}]}), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(older), "")))

    tab.load_snapshot_btn.click()

    assert "1 conclusions" in tab.snapshot_label.text()
    tab.diff_btn.click()
    wait_for(lambda: "an old conclusion" in tab.detail.toPlainText() and "Removed (1)" in tab.detail.toPlainText())


def test_load_snapshot_refuses_another_peers_snapshot(on_alice, tmp_path, monkeypatch):
    import json

    from PySide6.QtWidgets import QFileDialog

    tab = on_alice.peer_view.conclusions
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"workspace": "test-qwen", "peer": "agent", "taken_at": "2026-09-01T10:00:00",
                                 "conclusions": []}), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(other), "")))

    tab.load_snapshot_btn.click()

    assert "select that peer first" in on_alice.statusBar().currentMessage()
    assert tab.snapshot_label.text() == "No snapshot yet"


def _post_events(port, events):
    import json
    import urllib.request

    req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=json.dumps(events).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=5).read()


def _event(kind, ws, **data):
    return {"type": kind, "time": "t", "data": {"workspace_name": ws, **data}}


def _row(tab, ws):
    from PySide6.QtCore import Qt

    from honcho_viewer.monitor_view import COLUMNS

    for r in range(tab.table.rowCount()):
        if tab.table.item(r, 0).data(Qt.UserRole) == ws:
            return {name: tab.table.item(r, c).text() for c, (name, _) in enumerate(COLUMNS)} | {"_row": r}
    return None


def test_monitor_rows_appear_by_themselves_as_events_arrive(window):
    tab = window.monitor
    window.ctx.telemetry.start(0, "")
    port = window.ctx.telemetry.port
    assert tab.table.rowCount() == 0 and "Nothing yet" in tab.totals_label.text()

    _post_events(port, [
        _event("representation.completed", "run-a", message_count=12, explicit_conclusion_count=9, observed="p"),
        _event("llm.call.completed", "run-a", call_purpose="representation", model="haiku",
               provider_input_tokens=1500, provider_output_tokens=200, outcome="success", attempt=1),
        _event("dream.specialist", "run-b", specialist_type="deduction", created_observation_count=4,
               deleted_observation_count=1, success=True),
        _event("dialectic.completed", "run-c", peer_name="p", reasoning_level="low"),
    ])
    tab.refresh()  # the listener has already handed the events over by the time the post returns
    assert tab.table.rowCount() == 3

    a, b, c = _row(tab, "run-a"), _row(tab, "run-b"), _row(tab, "run-c")
    assert a["State"] == "● active"
    assert a["Extraction"].startswith("1 batches · 12 msgs") and a["LLM calls"].startswith("1 ·")
    assert a["Tokens in / out"] == "1.5k / 200" and a["Model(s)"] == "haiku"
    assert b["Dreaming"] == "0 runs · +4 −1" and c["Questions"] == "1"
    assert "<b>3</b> workspaces" in tab.totals_label.text() and "3 active" in tab.totals_label.text()
    window.ctx.telemetry.stop()


def test_monitor_flags_failed_calls_and_shows_them_in_the_detail_pane(window):
    tab = window.monitor
    window.ctx.telemetry.start(0, "")
    _post_events(window.ctx.telemetry.port, [
        _event("llm.call.completed", "run-a", call_purpose="dream", model="deepseek", provider_input_tokens=10,
               provider_output_tokens=0, outcome="error", error_class="RateLimitError", attempt=2),
        _event("dream.run", "run-a", dream_type="omni", observed="p", total_iterations=3),
    ])
    tab.refresh()

    row = _row(tab, "run-a")
    assert "1 failed" in row["Problems"] and "1 retried" in row["Problems"]
    assert "1 failed calls" in tab.totals_label.text()
    tab.table.selectRow(row["_row"])
    assert "RateLimitError" in tab.detail.toPlainText() and "dream" in tab.detail.toPlainText()
    assert "dream run (omni)" in tab.feed.toPlainText() and "ERROR dream on deepseek" in tab.feed.toPlainText()
    window.ctx.telemetry.stop()


def test_monitor_shows_queue_progress_by_polling_and_flags_a_silent_workspace_as_stalled(window):
    tab = window.monitor
    tab.set_workspaces(["test-qwen", "test-llama", "big"])
    tab.watch_combo.setCurrentIndex(0)
    tab.watch_btn.click()  # watching needs no telemetry at all
    wait_for(lambda: _row(tab, "test-qwen") and "2/3" in _row(tab, "test-qwen")["Queue"])

    row = _row(tab, "test-qwen")
    assert "1 run" in row["Queue"] and "0 wait" in row["Queue"]
    # the fake server reports work in progress, and nothing has ever been heard from this workspace
    assert row["State"] == "● STALLED" and "1 stalled" in tab.totals_label.text()


def test_monitor_stall_limit_is_saved_and_applied(window, tmp_path):
    tab = window.monitor
    tab.stall_spin.setValue(7)

    assert window.ctx.telemetry.monitor.stall_after == 420
    assert LocalStore(tmp_path).ui_value("monitor_stall_min") == 7


def test_monitor_watch_all_and_clear(window):
    tab = window.monitor
    tab.set_workspaces(["test-qwen", "test-llama", "big"])

    tab.watch_all_btn.click()
    assert tab.table.rowCount() == 3

    tab.clear_btn.click()
    assert tab.table.rowCount() == 0 and "Nothing yet" in tab.totals_label.text()


def test_monitor_and_compare_reasoning_share_one_listener(window):
    window.ctx.telemetry.start(0, "")

    assert window.monitor.panel.listen_btn.text() == "Stop listening"
    assert window.compare_reasoning.telemetry_panel.listen_btn.text() == "Stop listening"
    window.ctx.telemetry.stop()
    assert window.monitor.panel.listen_btn.text() == "Start listening"


def _fleet(window):
    """Four workspaces: one running, one with trouble, two finished long ago."""
    import time

    tab = window.monitor
    mon = window.ctx.telemetry.monitor
    now = time.time()
    mon.ingest([_event("representation.completed", "demo-run", message_count=5, explicit_conclusion_count=3,
                       observed="p")], received_at=now - 2)
    mon.set_queue("demo-run", {"total_work_units": 10, "completed_work_units": 4, "in_progress_work_units": 2,
                              "pending_work_units": 4})
    mon.ingest([_event("llm.call.completed", "hk-trouble", call_purpose="dream", model="m", outcome="error",
                       error_class="Timeout", provider_input_tokens=7, provider_output_tokens=1, attempt=1)],
               received_at=now - 3000)
    mon.set_queue("hk-trouble", {"total_work_units": 3, "completed_work_units": 3, "in_progress_work_units": 0,
                                 "pending_work_units": 0})
    for name in ("old-a", "old-b"):
        mon.ingest([_event("dream.run", name, dream_type="x", observed="p")], received_at=now - 5000)
        mon.set_queue(name, {"total_work_units": 2, "completed_work_units": 2, "in_progress_work_units": 0,
                             "pending_work_units": 0})
    tab.poll_check.setChecked(False)
    tab.refresh()
    return tab


def _visible_names(tab):
    from PySide6.QtCore import Qt

    return sorted(tab.table.item(r, 0).data(Qt.UserRole) for r in range(tab.table.rowCount())
                  if not tab.table.isRowHidden(r))


def test_monitor_by_default_shows_only_what_is_working_and_says_how_many_are_hidden(window):
    tab = _fleet(window)

    assert _visible_names(tab) == ["demo-run"]
    assert tab.count_label.text() == "Showing 1 of 4"

    tab.filter_combo.setCurrentIndex(tab.filter_combo.findData("all"))
    assert _visible_names(tab) == ["demo-run", "hk-trouble", "old-a", "old-b"]
    assert tab.count_label.text() == "Showing 4 of 4"


def test_monitor_quick_filters_pick_active_attention_and_finished(window):
    tab = _fleet(window)

    def pick(mode):
        tab.filter_combo.setCurrentIndex(tab.filter_combo.findData(mode))
        return _visible_names(tab)

    assert pick("active") == ["demo-run"]
    assert pick("attention") == ["hk-trouble"]  # the failed call, even though it finished long ago
    assert pick("done") == ["hk-trouble", "old-a", "old-b"]


def test_monitor_name_filter_accepts_several_words_separated_by_commas(window):
    tab = _fleet(window)
    tab.filter_combo.setCurrentIndex(tab.filter_combo.findData("all"))

    tab.text_filter.setText("demo, old-b")

    assert _visible_names(tab) == ["demo-run", "old-b"]
    tab.text_filter.setText("")
    assert len(_visible_names(tab)) == 4


def test_monitor_only_selected_pins_several_rows_and_toggles_back(window):
    from PySide6.QtCore import Qt
    from PySide6.QtCore import QItemSelectionModel as Sel

    tab = _fleet(window)
    tab.filter_combo.setCurrentIndex(tab.filter_combo.findData("all"))
    for r in range(tab.table.rowCount()):
        if tab.table.item(r, 0).data(Qt.UserRole) in ("old-a", "hk-trouble"):
            tab.table.selectionModel().select(tab.table.model().index(r, 0),
                                              Sel.Select | Sel.Rows)
    assert sorted(tab.selected_workspaces()) == ["hk-trouble", "old-a"]

    tab.only_selected.setChecked(True)
    assert _visible_names(tab) == ["hk-trouble", "old-a"]

    tab.only_selected.setChecked(False)
    assert len(_visible_names(tab)) == 4


def test_monitor_only_selected_does_nothing_without_a_selection(window):
    tab = _fleet(window)
    tab.only_selected.setChecked(True)

    assert not tab.only_selected.isChecked() and _visible_names(tab) == ["demo-run"]


def test_monitor_several_selected_rows_show_a_combined_summary_and_merged_feed(window):
    from PySide6.QtCore import Qt
    from PySide6.QtCore import QItemSelectionModel as Sel

    tab = _fleet(window)
    tab.filter_combo.setCurrentIndex(tab.filter_combo.findData("all"))
    for r in range(tab.table.rowCount()):
        if tab.table.item(r, 0).data(Qt.UserRole) in ("demo-run", "hk-trouble"):
            tab.table.selectionModel().select(tab.table.model().index(r, 0), Sel.Select | Sel.Rows)

    tab.refresh()

    text = tab.detail.toPlainText()
    assert "2 workspaces selected" in text and "demo-run" in text and "hk-trouble" in text
    assert "total" in text
    feed = tab.feed.toPlainText()
    assert "[demo-run] extraction" in feed and "[hk-trouble] ERROR dream on m" in feed
    assert feed.index("[demo-run]") < feed.index("[hk-trouble]")  # newest first across workspaces


def test_monitor_remembers_the_chosen_filter(window, tmp_path):
    window.monitor.filter_combo.setCurrentIndex(window.monitor.filter_combo.findData("attention"))

    assert LocalStore(tmp_path).ui_value("monitor_filter") == "attention"


def _visual_titles(tab):
    header = tab.table.horizontalHeader()
    return [tab.table.horizontalHeaderItem(header.logicalIndex(v)).text() for v in range(header.count())]


def test_monitor_columns_can_be_dragged_and_the_order_is_remembered(window, tmp_path):
    from honcho_viewer.main_window import MainWindow

    tab = window.monitor
    assert _visual_titles(tab)[:3] == ["Workspace", "State", "Last event"]

    header = tab.table.horizontalHeader()
    header.moveSection(header.visualIndex(5), 1)  # drag "Dreaming" right after Workspace
    assert _visual_titles(tab)[:3] == ["Workspace", "Dreaming", "State"]

    fresh = MainWindow(LocalStore(tmp_path))
    assert _visual_titles(fresh.monitor)[:3] == ["Workspace", "Dreaming", "State"]
    fresh.close()


def test_monitor_data_stays_in_the_right_column_after_rearranging(window):
    tab = _fleet(window)
    tab.filter_combo.setCurrentIndex(tab.filter_combo.findData("all"))
    tab.table.horizontalHeader().moveSection(10, 0)  # put "Model(s)" first

    row = _row(tab, "demo-run")

    assert row["State"] == "● active" and row["Extraction"].startswith("1 batches")
    assert _visible_names(tab) == ["demo-run", "hk-trouble", "old-a", "old-b"]


def test_monitor_reset_column_order_restores_the_default(window, tmp_path):
    tab = window.monitor
    tab.table.horizontalHeader().moveSection(3, 0)
    assert _visual_titles(tab)[0] != "Workspace"

    tab.reset_order()

    assert _visual_titles(tab)[0] == "Workspace" and _visual_titles(tab)[1] == "State"
    assert LocalStore(tmp_path).ui_value("monitor_columns") == list(range(11))


def test_monitor_ignores_a_saved_order_that_no_longer_fits_the_columns(tmp_path, qapp, fake):
    from honcho_viewer.main_window import MainWindow

    store = LocalStore(tmp_path)
    store.set_connection(fake.url, TOKEN)
    store.set_ui_value("monitor_columns", [0, 1, 2])  # e.g. saved by an older version with fewer columns

    win = MainWindow(store)

    assert _visual_titles(win.monitor)[:2] == ["Workspace", "State"]
    win.close()


def _peer_order(cp):
    return [cp.cell("Peer", i) for i in range(cp.counts_table.columnCount())]


def _peers_loaded(window):
    cp = _open_peers(window)
    cp.load_btn.click()
    cp.show_combo.setCurrentText("Counts")
    wait_for(lambda: cp.counts_table.columnCount() == 3 and cp.cell("Conclusions", 2) not in ("", "…"))
    return cp


def test_columns_can_be_moved_left_and_right_and_edges_are_disabled(window):
    cp = _peers_loaded(window)
    before = _peer_order(cp)
    cp.show_combo.setCurrentText("Answers")
    first, last = cp.columns._arrows[0], cp.columns._arrows[2]
    assert not first[0].isEnabled() and first[1].isEnabled()  # the first column cannot go further left
    assert last[0].isEnabled() and not last[1].isEnabled()

    first[1].click()  # move column 0 one step right

    cp.show_combo.setCurrentText("Counts")
    assert _peer_order(cp) == [before[1], before[0], before[2]]
    cp.show_combo.setCurrentText("Answers")
    cp.columns._arrows[2][0].click()  # and the last one back to the left
    cp.show_combo.setCurrentText("Counts")
    assert _peer_order(cp) == [before[1], before[2], before[0]]


def test_asking_again_keeps_the_order_i_chose(window):
    cp = _peers_loaded(window)
    cp.show_combo.setCurrentText("Answers")
    cp.columns._arrows[0][1].click()
    cp.show_combo.setCurrentText("Counts")
    moved = _peer_order(cp)

    cp.question.setText("again?")
    cp.ask_btn.click()  # must not look like a new selection and reload everything in the default order
    wait_for(lambda: all("answer to: again?" in cp.columns.text(i) for i in range(3)))

    cp.show_combo.setCurrentText("Counts")
    assert _peer_order(cp) == moved


def test_a_saved_page_keeps_the_column_order_and_reopens_that_way(window, tmp_path, monkeypatch):
    import json
    import re

    from honcho_viewer.main_window import MainWindow

    cp = _peers_loaded(window)
    before = _peer_order(cp)
    cp.show_combo.setCurrentText("Answers")
    cp.columns._arrows[0][1].click()
    cp.save_btn.click()
    page = _saved_pages(tmp_path)[0]
    data = json.loads(re.search(r'<script type="application/json" id="comparison-data">(.*?)</script>',
                                page.read_text(encoding="utf-8"), re.S).group(1))
    assert list(data["peers"]) == [before[1], before[0], before[2]]

    fresh = MainWindow(LocalStore(tmp_path))
    wait_for(lambda: fresh.workspace_list.count() == 3)
    _open(fresh.compare_peers, page, monkeypatch)
    assert _peer_order(fresh.compare_peers) == [before[1], before[0], before[2]]
    fresh.close()


def test_moving_columns_works_on_compare_models_and_reasoning_too(window):
    cmp = _load_models_tab(window)  # one workspace: a single column, so both arrows are off
    cmp.show_combo.setCurrentText("Answers")
    assert not any(b.isEnabled() for b in cmp.columns._arrows[0])

    cr = _open_reasoning(window)
    cr.set_levels(["low", "high"])
    cr.question.setText("hi")
    cr.ask_btn.click()
    cr.show_combo.setCurrentText("Stats")
    wait_for(lambda: cr.cell("Answer words", 1) not in ("", "…"))
    cr.show_combo.setCurrentText("Answers")
    cr.columns._arrows[0][1].click()  # put "high" before "low"
    cr.show_combo.setCurrentText("Stats")

    assert [cr.cell("Reasoning level", i) for i in range(2)] == ["high", "low"]
    assert cr.cell("Answer words", 0) not in ("", "…") and cr.cell("Wall time (s)", 1) not in ("", "…")


def test_viewer_never_calls_unknown_routes(on_alice, fake):
    assert fake.unexpected == []
