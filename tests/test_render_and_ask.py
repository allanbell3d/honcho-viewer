from honcho_viewer import render
from honcho_viewer.ask import build_chat_body


def _c(cid, level="explicit", content="likes sailing", sources=()):
    return {"id": cid, "level": level, "content": content, "observer_id": "alice", "observed_id": "alice",
            "session_id": "s1", "source_ids": list(sources), "times_derived": 1,
            "created_at": "2026-10-01T10:00:00Z"}


# ---- render ---------------------------------------------------------------
def test_text_is_html_escaped():
    assert render.esc("<b>hi</b>\nthere") == "&lt;b&gt;hi&lt;/b&gt;<br>there"


def test_card_placeholder_when_missing():
    assert "No peer card" in render.card_html(None)
    assert "<li>lives in Lisbon</li>" in render.card_html(["lives in Lisbon"])


def test_origin_splits_messages_from_dreaming():
    assert render.origin("explicit") == "messages"
    assert {render.origin(lv) for lv in ("deductive", "inductive", "contradiction")} == {"dreaming"}


def test_conclusion_list_groups_by_origin_and_marks_ratings():
    html = render.conclusions_html([_c("a"), _c("b", "deductive", "<x>")], {"a": "correct"})

    assert html.index("From messages") < html.index("From dreaming")
    assert "✓" in html and "&lt;x&gt;" in html


def test_conclusion_detail_shows_premises_and_supporting_messages():
    premise = _c("p", content="lives in Lisbon")
    msg = {"peer_id": "alice", "content": "I live in Lisbon", "created_at": "2026-10-01T10:00:00Z",
           "session_id": "s1"}

    html = render.conclusion_detail_html(_c("d", "deductive", sources=["p"]), [premise], [msg])

    assert "Built from" in html and "lives in Lisbon" in html
    assert "I live in Lisbon" in html


def test_answer_shows_elapsed_and_evidence():
    resp = {"content": "In <Lisbon>", "elapsed_s": 3.2,
            "evidence": {"conclusions": [_c("a")], "messages": [{"id": "m1", "session_id": "s1",
                                                                  "peer_id": "alice", "created_at": "x"}],
                         "tool_calls": [{"tool_name": "search_memory", "tool_input": {"q": "home"}}]}}

    html = render.answer_html("where?", resp)

    assert "In &lt;Lisbon&gt;" in html and "3.2 s" in html
    assert "search_memory" in html and "likes sailing" in html


def test_diff_lists_added_and_removed():
    html = render.diff_html({"added": [_c("n", "inductive", "new idea")], "removed": [_c("o", content="old")],
                             "kept": 4}, "2026-10-06T09:00:00")

    assert "new idea" in html and "old" in html and "4 unchanged" in html


def test_queue_text():
    assert render.queue_text({"pending_work_units": 0, "in_progress_work_units": 0}) == "idle"
    assert render.queue_text({"pending_work_units": 2, "in_progress_work_units": 1}) == "2 waiting · 1 processing"


# ---- ask ------------------------------------------------------------------
def test_chat_body_with_defaults_omits_empty_options():
    body, error = build_chat_body("where?", "low", True)

    assert error is None
    assert body == {"query": "where?", "reasoning_level": "low", "include_evidence": True}


def test_chat_body_includes_optional_fields():
    body, _ = build_chat_body("q", "high", False, session_id="s1", target="agent",
                              response_format='{"type": "object"}')

    assert body["session_id"] == "s1" and body["target"] == "agent"
    assert body["response_format"] == {"type": "object"}


def test_chat_body_scope_list_from_commas():
    body, _ = build_chat_body("q", "low", False, scope="a, b")
    assert body["scope"] == ["a", "b"]
    body, _ = build_chat_body("q", "low", False, scope="a")
    assert body["scope"] == "a"


def test_chat_body_errors():
    assert build_chat_body("  ", "low", False)[1] == "Type a question first."
    assert "not both" in build_chat_body("q", "low", False, session_id="s", scope="x")[1]
    assert "JSON" in build_chat_body("q", "low", False, response_format="{bad")[1]
