"""History: reading log files in every accepted format, and grouping events into jobs."""
import gzip
import itertools
import json

from honcho_viewer.history import HistoryState, iter_log_file, log_files

WS = "demo"
_ids = itertools.count()


def ev(kind, t, **data):
    """A CloudEvent at ``t`` seconds past 2026-10-09 10:00 UTC."""
    minutes, seconds = divmod(t, 60)
    return {"specversion": "1.0", "id": f"evt_{next(_ids)}", "type": kind,
            "time": f"2026-10-09T10:{int(minutes):02d}:{seconds:06.3f}+00:00",
            "data": {"workspace_name": WS, **data}}


def wrap(event):
    return json.dumps({"received_at": event["time"], "event": event})


def state(*events):
    h = HistoryState()
    h.add_events(list(events))
    return h


def only(h, kind):
    jobs = h.select(WS, {kind})
    assert len(jobs) == 1, jobs
    return jobs[0]


# ---- reading ---------------------------------------------------------------------------------------------
def test_reads_wrapper_lines_plain_events_arrays_and_gzip(tmp_path):
    a, b, c, d = (ev("message.created", i, message_count=1) for i in range(4))
    (tmp_path / "events-1.jsonl").write_text(f"{wrap(a)}\n{json.dumps(b)}\n{json.dumps([c])}\n", encoding="utf-8")
    with gzip.open(tmp_path / "events-0.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(wrap(d) + "\n")

    h = HistoryState()
    counts = h.load_paths([tmp_path])

    assert counts == {"files": 2, "events": 4, "bad": 0}
    assert len(h.select(WS)) == 4


def test_bad_lines_are_counted_not_fatal_and_trace_content_is_skipped(tmp_path):
    good = ev("message.created", 1, message_count=2)
    prompt = ev("trace.content", 2, content="a very long prompt")
    (tmp_path / "log.jsonl").write_text(f"{{broken\n{wrap(prompt)}\n{wrap(good)}\n", encoding="utf-8")

    h = HistoryState()
    counts = h.load_paths([tmp_path / "log.jsonl"])

    assert counts["bad"] == 1 and counts["events"] == 1
    assert [j.kind for j in h.select(WS)] == ["messages"]


def test_a_growing_file_is_read_from_where_it_stopped_and_a_half_written_line_waits(tmp_path):
    path = tmp_path / "events.jsonl"
    first, second = ev("message.created", 1, message_count=1), ev("message.created", 2, message_count=1)
    path.write_text(wrap(first) + "\n" + wrap(second)[:20], encoding="utf-8")  # second line still being written
    h = HistoryState()
    assert h.load_paths([tmp_path])["events"] == 1

    path.write_text(wrap(first) + "\n" + wrap(second) + "\n", encoding="utf-8")
    assert h.refresh_files()["events"] == 1
    assert h.refresh_files()["events"] == 0  # nothing new
    assert len(h.select(WS)) == 2


def test_the_same_event_from_a_file_and_from_the_listener_counts_once(tmp_path):
    event = ev("message.created", 1, message_count=3)
    (tmp_path / "events.jsonl").write_text(wrap(event) + "\n", encoding="utf-8")
    h = HistoryState()
    h.add_events([event])
    h.load_paths([tmp_path])
    assert len(h.select(WS)) == 1


def test_log_files_finds_logs_in_folders_and_ignores_other_files(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ("a.jsonl", "sub/b.jsonl.gz", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert sorted(f.name for f in log_files([tmp_path])) == ["a.jsonl", "b.jsonl.gz"]


def test_iter_log_file_reports_the_offset_reached(tmp_path):
    path = tmp_path / "e.jsonl"
    path.write_text(wrap(ev("message.created", 1)) + "\n", encoding="utf-8")
    *_, offset = iter_log_file(path)
    assert offset == path.stat().st_size


# ---- questions and dreams (grouped by run_id) ------------------------------------------------------------
def _call(t, run=None, outcome="success", attempt=1, final=True, purpose="dialectic.answer", category="dialectic",
          **extra):
    return ev("llm.call.completed", t, run_id=run, parent_category=category, call_purpose=purpose, model="m1",
              outcome=outcome, attempt=attempt, is_final_attempt=final, provider_input_tokens=100,
              provider_output_tokens=10, duration_ms=500, error_class="APIStatusError" if outcome == "error" else None,
              **extra)


def test_a_question_is_ok_with_its_own_token_totals():
    h = state(_call(1, "q1"), ev("dialectic.completed", 2, run_id="q1", peer_name="alice", reasoning_level="low",
                                 input_tokens=900, output_tokens=40, total_duration_ms=2000))
    job = only(h, "question")
    assert job.status == "ok" and job.peer == "alice"
    assert (job.tokens_in, job.tokens_out, job.calls) == (900, 40, 1)


def test_a_question_that_gave_up_is_failed_with_its_error_and_its_peer():
    h = state(_call(1, "q2", "error", 1, False), _call(2, "q2", "error", 2, False), _call(3, "q2", "error", 3, True),
              ev("llm.call.traced", 3, run_id="q2", peer_name="bob", call_purpose="dialectic.answer"))
    job = only(h, "question")
    assert job.status == "failed" and job.final_error == "APIStatusError"
    assert (job.errors, job.retries, job.calls, job.peer) == (3, 2, 3, "bob")  # traced twin is not counted again


def test_a_question_that_worked_after_a_failed_attempt_is_retried():
    h = state(_call(1, "q3", "error", 1, False), _call(2, "q3", attempt=2),
              ev("dialectic.completed", 3, run_id="q3", peer_name="alice"))
    assert only(h, "question").status == "retried"


def test_a_dream_adds_up_its_specialists_and_flags_one_that_failed():
    h = state(
        ev("dream.specialist", 10, run_id="d1", specialist_type="deduction", success=True, observer="a", observed="a",
           created_counts_by_level={"deductive": 3}, deleted_counts_by_level={"explicit": 1}, peer_card_updated=True),
        ev("dream.specialist", 20, run_id="d1", specialist_type="induction", success=False, observer="a",
           observed="a", error_class="Timeout"),
        ev("dream.run", 21, run_id="d1", dream_type="omni", observer="a", observed="a", total_input_tokens=5000,
           total_output_tokens=300, total_duration_ms=20000))
    job = only(h, "dream")
    assert job.status == "partly failed" and job.problem
    assert job.created == {"deductive": 3} and job.deleted == {"explicit": 1} and job.card_updated
    assert job.tokens_in == 5000 and round(job.duration) == 20


def test_a_run_that_has_not_finished_and_has_not_failed_is_incomplete():
    assert only(state(_call(1, "q4")), "question").status == "incomplete"


# ---- extraction (no run_id: linked by message id) ----------------------------------------------------------
def _traced(t, trace, messages, outcome="success", attempt=1, final=True, purpose="deriver.representation"):
    return ev("llm.call.traced", t, run_id=None, trace_id=trace, call_purpose=purpose, model="m2", outcome=outcome,
              attempt=attempt, is_final_attempt=final, provider_input_tokens=1000, provider_output_tokens=50,
              source_message_ids=messages, observed="alice", duration_ms=800,
              error_class="APIStatusError" if outcome == "error" else None)


def _batch(t, first, last):
    return ev("representation.completed", t, observed="alice", session_name="s1", earliest_message_id=first,
              latest_message_id=last, message_count=4, explicit_conclusion_count=6, total_input_tokens=1000,
              output_tokens=50, total_duration_ms=1500)


def test_an_extraction_call_and_its_batch_event_are_one_job_whichever_arrives_first():
    for events in ([_traced(1, "t1", ["m4"]), _batch(2, "m1", "m4")],
                   [_batch(2, "m1", "m4"), _traced(1, "t1", ["m4"])]):
        job = only(state(*events), "extraction")
        assert job.status == "ok" and job.created == {"explicit": 6} and job.messages == 4
        assert job.calls == 1 and job.models == ["m2"] and job.session == "s1"


def test_an_extraction_that_gave_up_is_one_failed_job_with_its_retries():
    h = state(_traced(1, "t2", ["m9"], "error", 1, False), _traced(5, "t2", ["m9"], "error", 2, True))
    job = only(h, "extraction")
    assert job.status == "failed" and (job.errors, job.retries) == (2, 1)


def test_the_plain_completed_twin_of_a_traced_call_is_not_counted_twice():
    h = state(_traced(1, "t3", ["m1"]), _call(1, None, purpose="deriver.representation", category="representation"),
              _batch(2, "m1", "m1"))
    assert only(h, "extraction").calls == 1


def test_without_traced_events_calls_are_still_counted_on_their_own():
    h = state(_call(1, None, purpose="deriver.representation", category="representation"), _batch(2, "m1", "m1"))
    assert {j.kind for j in h.select(WS)} == {"extraction"}
    assert sum(j.calls for j in h.select(WS)) == 1


def test_a_summary_call_reading_the_same_messages_is_not_mixed_into_an_extraction():
    h = state(_traced(1, "t4", ["m1"]), _traced(2, "t5", ["m1"], purpose="summary.short"), _batch(3, "m1", "m1"))
    assert only(h, "extraction").calls == 1


def test_a_summary_call_and_its_summary_event_match_on_input_tokens_in_either_order():
    created = ev("agent.tool.summary.created", 3, summary_type="short", input_tokens=1000, output_tokens=50,
                 message_count=20, session_name="s1")
    for events in ([_traced(2, "t6", ["m1"], purpose="summary.short"), created],
                   [created, _traced(2, "t6", ["m1"], purpose="summary.short")]):
        job = only(state(*events), "summary")
        assert job.status == "ok" and job.calls == 1 and job.session == "s1"


# ---- other events and queries ----------------------------------------------------------------------------
def test_a_deletion_shows_what_it_removed():
    h = state(ev("deletion.completed", 1, deletion_type="session", resource_id="s1", success=True,
                 messages_deleted=4, conclusions_deleted=12))
    job = only(h, "deletion")
    assert job.status == "ok" and job.deleted == {"conclusions": 12} and job.messages == -4


def test_select_filters_by_peer_session_time_and_problems_and_totals_add_up():
    h = state(_call(1, "q1"), ev("dialectic.completed", 2, run_id="q1", peer_name="alice", input_tokens=10,
                                 output_tokens=1, session_name="s1"),
              _call(600, "q2", "error"), ev("llm.call.traced", 600, run_id="q2", peer_name="bob"),
              _traced(900, "t1", ["m1"]), _batch(901, "m1", "m1"))
    assert len(h.select(WS)) == 3
    assert [j.peer for j in h.select(WS, peer="bob")] == ["bob"]
    assert [j.kind for j in h.select(WS, session="s1")] == ["extraction", "question"]  # newest first
    assert [j.peer for j in h.select(WS, problems_only=True)] == ["bob"]
    first_minute_end = h.select(WS, {"question"})[-1].end + 1
    assert len(h.select(WS, end=first_minute_end)) == 1
    t = HistoryState.totals(h.select(WS))
    assert (t["jobs"], t["failed"], t["created"]) == (3, 1, 6)
    assert h.peers(WS) == ["alice", "bob"] and h.workspaces() == [WS]
