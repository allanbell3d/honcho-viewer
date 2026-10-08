"""The telemetry listener, log and run matching (no GUI)."""
import json
import time
import urllib.error
import urllib.request

import pytest

from honcho_viewer.telemetry import (EventLog, TelemetryReceiver, TelemetryStore, extract_events, parse_allowed,
                                     summarize_run)


def dialectic(run="r1", ws="w", peer="p", level="low", **extra):
    data = {"run_id": run, "workspace_name": ws, "peer_name": peer, "reasoning_level": level,
            "input_tokens": 1000, "output_tokens": 50, "cache_read_tokens": 400, "total_duration_ms": 12345.6,
            "total_iterations": 3, "tool_calls_count": 4, "prefetched_conclusion_count": 9, **extra}
    return {"type": "dialectic.completed", "time": "2026-10-08T00:00:00Z", "data": data}


def llm_call(run="r1", model="haiku"):
    return {"type": "llm.call.completed", "time": "2026-10-08T00:00:00Z",
            "data": {"run_id": run, "model": model, "provider_input_tokens": 10, "provider_output_tokens": 2}}


def post(port, payload, key=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if key:
        headers["X-Telemetry-Key"] = key
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/events", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


@pytest.fixture
def receiver(tmp_path):
    store, log = TelemetryStore(), EventLog(tmp_path / "telemetry")
    rec = TelemetryReceiver(store, log)
    rec.start(0, host="127.0.0.1")
    yield rec
    rec.stop()


def test_extract_events_accepts_single_array_and_log_line_wrapper():
    one = dialectic()
    assert extract_events(one) == [one]
    assert extract_events([one, llm_call()]) == [one, llm_call()]
    assert extract_events({"received_at": "x", "event": one}) == [one]
    assert extract_events([{"received_at": "x", "event": one}]) == [one]
    assert extract_events({"nothing": "here"}) == [] and extract_events("junk") == []


def test_receiver_stores_events_in_memory_and_logs_every_one_in_full(receiver, tmp_path):
    big = {"type": "trace.content", "time": "t", "data": {"run_id": "r1", "content": "secret prompt " * 1000}}

    assert post(receiver.port, [dialectic(), llm_call(), big]) == 202
    assert post(receiver.port, dialectic(run="r2"), content_type="application/cloudevents+json") == 202

    assert receiver.store.total == 4
    kept = {e["type"]: e for _, e in receiver.store.snapshot()}
    assert "content" not in kept["trace.content"]["data"]  # bulky payloads stay out of memory ...
    log = next((tmp_path / "telemetry").glob("events-*.jsonl")).read_text(encoding="utf-8").splitlines()
    assert len(log) == 4
    logged_big = [json.loads(line)["event"] for line in log if "trace.content" in line][0]
    assert logged_big["data"]["content"].startswith("secret prompt")  # ... but the log has everything


def test_receiver_rejects_wrong_key_and_garbage(tmp_path):
    rec = TelemetryReceiver(TelemetryStore(), EventLog(tmp_path), key="s3cret")
    rec.start(0, host="127.0.0.1")
    try:
        with pytest.raises(urllib.error.HTTPError) as no_key:
            post(rec.port, dialectic())
        assert no_key.value.code == 401
        assert post(rec.port, dialectic(), key="s3cret") == 202
        with pytest.raises(urllib.error.HTTPError) as junk:
            post(rec.port, b"not json", key="s3cret")
        assert junk.value.code == 400 and rec.bad_posts == 1
        assert rec.store.total == 1
    finally:
        rec.stop()


def test_stop_releases_the_port(tmp_path):
    rec = TelemetryReceiver(TelemetryStore())
    port = rec.start(0, host="127.0.0.1")
    rec.stop()
    assert not rec.running
    again = TelemetryReceiver(TelemetryStore())
    assert again.start(port, host="127.0.0.1") == port
    again.stop()


def test_find_dialectic_matches_workspace_peer_level_and_time_and_claims():
    store = TelemetryStore()
    t0 = time.time()
    store.add([dialectic(run="old", level="high")], received_at=t0 - 100)
    store.add([dialectic(run="a", level="high"), dialectic(run="b", level="low"),
               dialectic(run="c", level="high", peer="someone-else")], received_at=t0 + 1)
    store.add([dialectic(run="d", level="high")], received_at=t0 + 5)

    assert store.find_dialectic("w", "p", "high", since=t0)["data"]["run_id"] == "a"  # not the older one
    assert store.find_dialectic("w", "p", "high", since=t0, claimed={"a"})["data"]["run_id"] == "d"
    assert store.find_dialectic("w", "p", "max", since=t0) is None
    assert store.find_dialectic("w", "nobody", "high", since=t0) is None


def test_summarize_run_gathers_tokens_time_and_models():
    store = TelemetryStore()
    store.add([llm_call("r1", "haiku"), llm_call("r1", "haiku"), llm_call("r1", "deepseek"), llm_call("other"),
               dialectic("r1")])
    event = store.find_dialectic("w", "p", "low", since=0)

    s = summarize_run(event, store.run_events("r1"))

    assert (s["input_tokens"], s["output_tokens"], s["cache_read_tokens"]) == (1000, 50, 400)
    assert s["server_s"] == 12.3 and s["iterations"] == 3 and s["tool_calls"] == 4 and s["prefetched"] == 9
    assert s["llm_calls"] == 3 and s["models"] == ["haiku", "deepseek"]


def test_log_prunes_oldest_days_past_the_cap_but_never_the_current_one(tmp_path):
    log = EventLog(tmp_path, max_bytes=2000)
    day = 86400
    now = time.time()
    for back in (3, 2, 1, 0):
        log.write([{"type": "x", "data": {"pad": "y" * 900}}], received_at=now - back * day)
        log._last_prune = 0  # let every write prune in this test

    names = sorted(p.name for p in tmp_path.glob("events-*.jsonl"))
    assert 1 <= len(names) <= 2
    newest = time.strftime("events-%Y%m%d.jsonl", time.gmtime(now))
    assert newest in names


def test_parse_allowed_accepts_commas_spaces_and_blank():
    assert parse_allowed("192.168.1.22, 10.0.0.5;  ::1") == {"192.168.1.22", "10.0.0.5", "::1"}
    assert parse_allowed("  ") == frozenset()


def test_receiver_only_accepts_listed_sender_addresses(tmp_path):
    blocked = TelemetryReceiver(TelemetryStore(), allowed=frozenset({"192.168.200.200"}))
    blocked.start(0, host="127.0.0.1")
    open_to_loopback = TelemetryReceiver(TelemetryStore(), allowed=frozenset({"127.0.0.1"}))
    open_to_loopback.start(0, host="127.0.0.1")
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            post(blocked.port, dialectic())
        assert err.value.code == 403 and blocked.refused == 1 and blocked.store.total == 0
        assert post(open_to_loopback.port, dialectic()) == 202
    finally:
        blocked.stop()
        open_to_loopback.stop()


def test_a_second_listener_on_the_same_port_is_refused_not_shared(tmp_path):
    first = TelemetryReceiver(TelemetryStore())
    port = first.start(0, host="127.0.0.1")
    second = TelemetryReceiver(TelemetryStore())
    try:
        with pytest.raises(OSError):
            second.start(port, host="127.0.0.1")
        assert not second.running
        assert post(port, dialectic()) == 202 and first.store.total == 1  # the first one still gets everything
    finally:
        first.stop()
