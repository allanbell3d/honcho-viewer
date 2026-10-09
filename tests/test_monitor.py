"""The per-workspace telemetry aggregator behind the Monitor tab (no GUI)."""
from honcho_viewer.monitor import NO_WORKSPACE, MonitorState


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def ev(kind, ws="w1", **data):
    return {"type": kind, "time": "t", "data": {"workspace_name": ws, **data}}


def llm(ws="w1", purpose="representation", model="m", ins=100, outs=10, outcome="success", **extra):
    return ev("llm.call.completed", ws, call_purpose=purpose, model=model, provider_input_tokens=ins,
              provider_output_tokens=outs, outcome=outcome, **{"attempt": 1, **extra})


def test_rows_appear_dynamically_one_per_workspace_that_sends_events():
    state = MonitorState(clock=Clock())
    assert state.workspaces() == []

    state.ingest([llm("a"), llm("b"), llm("a")])

    assert sorted(state.workspaces()) == ["a", "b"]
    assert state.snapshot()["a"]["calls"] == 2 and state.snapshot()["b"]["calls"] == 1


def test_events_without_a_workspace_are_kept_under_their_own_row():
    state = MonitorState(clock=Clock())
    state.ingest([{"type": "llm.call.completed", "data": {"provider_input_tokens": 5}}])

    assert state.workspaces() == [NO_WORKSPACE] and state.snapshot()[NO_WORKSPACE]["tokens_in"] == 5


def test_extraction_dreaming_and_questions_are_counted_separately():
    state = MonitorState(clock=Clock())
    state.ingest([
        ev("representation.completed", message_count=12, explicit_conclusion_count=9, observed="p"),
        ev("representation.completed", message_count=8, explicit_conclusion_count=5, observed="p",
           failed_observer_count=1),
        ev("dream.specialist", specialist_type="deduction", created_observation_count=4, deleted_observation_count=1,
           success=True),
        ev("dream.specialist", specialist_type="induction", success=False, error_class="Timeout"),
        ev("dream.run", dream_type="omni", observed="p"),
        ev("dialectic.completed", peer_name="p", reasoning_level="low"),
        ev("message.created"), ev("message.created"),
    ])

    row = state.snapshot()["w1"]
    assert (row["extract_batches"], row["extract_messages"], row["extract_conclusions"]) == (2, 20, 14)
    assert row["extract_failed_observers"] == 1
    assert (row["dream_runs"], row["specialists"], row["specialists_failed"]) == (1, 2, 1)
    assert (row["dream_created"], row["dream_deleted"]) == (4, 1)
    assert row["questions"] == 1 and row["messages_created"] == 2
    assert row["last_kind"] == "questions"  # the last event that says what it is doing
    assert state.errors("w1")[0]["error"] == "Timeout"


def test_llm_calls_track_tokens_purposes_models_errors_retries_and_fallbacks():
    state = MonitorState(clock=Clock())
    state.ingest([
        llm(purpose="representation", model="haiku", ins=1000, outs=100),
        llm(purpose="dialectic", model="haiku", ins=500, outs=50),
        llm(purpose="representation", model="deepseek", ins=10, outs=1, outcome="error", error_class="RateLimit",
            attempt=2),
        llm(purpose="dream", model="haiku", was_fallback=True),
    ])

    row = state.snapshot()["w1"]
    assert (row["calls"], row["tokens_in"], row["tokens_out"]) == (4, 1610, 161)
    assert row["errors"] == 1 and row["retries"] == 1 and row["fallbacks"] == 1
    assert row["models"][0] == "haiku" and set(row["models"]) == {"haiku", "deepseek"}
    assert state.purposes("w1")["representation"] == {"calls": 2, "in": 1010, "out": 101, "errors": 1}
    assert any("RateLimit" in line for _, line in state.recent("w1"))


def test_state_active_then_idle_by_time_since_the_last_event():
    clock = Clock()
    state = MonitorState(clock=clock)
    state.ingest([llm()])
    assert state.snapshot()["w1"]["state"] == "active"

    clock.now += 60
    assert state.snapshot()["w1"]["state"] == "idle"


def test_stalled_means_work_is_waiting_but_nothing_has_been_heard_for_a_while():
    clock = Clock()
    state = MonitorState(clock=clock, stall_after=120)
    state.ingest([llm()])
    state.set_queue("w1", {"total_work_units": 10, "completed_work_units": 4, "in_progress_work_units": 1,
                           "pending_work_units": 5})
    clock.now += 60
    assert state.snapshot()["w1"]["state"] == "idle"  # quiet, but not long enough to worry

    clock.now += 100
    row = state.snapshot()["w1"]
    assert row["state"] == "stalled"
    assert (row["queue_done"], row["queue_total"], row["queue_running"], row["queue_waiting"]) == (4, 10, 1, 5)


def test_a_workspace_with_pending_work_and_no_events_at_all_is_stalled_not_missing():
    state = MonitorState(clock=Clock())
    state.set_queue("quiet", {"total_work_units": 3, "completed_work_units": 0, "in_progress_work_units": 0,
                              "pending_work_units": 3})

    assert state.snapshot()["quiet"]["state"] == "stalled"


def test_finished_queue_with_no_new_events_is_done():
    clock = Clock()
    state = MonitorState(clock=clock)
    state.ingest([llm()])
    state.set_queue("w1", {"total_work_units": 3, "completed_work_units": 3, "in_progress_work_units": 0,
                           "pending_work_units": 0})
    clock.now += 60

    assert state.snapshot()["w1"]["state"] == "done"


def test_rates_use_only_the_last_minute_and_totals_add_up_the_fleet():
    clock = Clock()
    state = MonitorState(clock=clock)
    state.ingest([llm("a", ins=100, outs=0)], received_at=clock.now - 90)  # too old for the rate
    state.ingest([llm("a", ins=200, outs=0), llm("b", ins=300, outs=0, outcome="error")], received_at=clock.now)

    assert state.snapshot()["a"]["calls_per_min"] == 1 and state.snapshot()["a"]["tokens_per_min"] == 200
    totals = state.totals()
    assert (totals["workspaces"], totals["calls"], totals["tokens_in"], totals["errors"]) == (2, 3, 600, 1)
    assert totals["active"] == 2 and totals["events_per_s"] == 0.2


def test_detail_feed_is_newest_first_bounded_and_skips_bulky_trace_events():
    state = MonitorState(clock=Clock(), recent=3)
    state.ingest([ev("dream.run", dream_type="a"), ev("dream.run", dream_type="b"),
                  ev("trace.content"), ev("llm.call.traced"),
                  ev("dream.run", dream_type="c"), ev("dream.run", dream_type="d")])

    lines = [text for _, text in state.recent("w1")]
    assert len(lines) == 3
    assert "(d)" in lines[0] and "(c)" in lines[1] and "(b)" in lines[2]
    assert not any("trace" in line for line in lines)
    assert state.snapshot()["w1"]["events"] == 6  # they still count as events


def test_watch_adds_a_row_and_clear_removes_everything():
    state = MonitorState(clock=Clock())
    state.watch("early")
    assert state.workspaces() == ["early"] and state.snapshot()["early"]["state"] == "idle"

    state.clear()
    assert state.workspaces() == [] and state.totals()["workspaces"] == 0
