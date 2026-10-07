import threading

from honcho_viewer.async_call import Latest, run_async
from tests.conftest import wait_for


def test_result_is_delivered_on_the_gui_thread(qapp):
    seen = {}
    run_async(lambda: threading.get_ident(),
              lambda worker_id: seen.update(worker=worker_id, callback=threading.get_ident()),
              lambda exc: seen.update(error=exc))

    wait_for(lambda: seen)
    assert seen["worker"] != threading.get_ident()
    assert seen["callback"] == threading.get_ident()


def test_exceptions_go_to_the_error_callback(qapp):
    seen = []

    def boom():
        raise RuntimeError("nope")

    run_async(boom, lambda r: seen.append(("ok", r)), lambda exc: seen.append(("err", str(exc))))

    wait_for(lambda: seen)
    assert seen == [("err", "nope")]


def test_latest_ticket_goes_stale_when_superseded():
    latest = Latest()
    first = latest.ticket("peer")
    other_key = latest.ticket("ws")
    second = latest.ticket("peer")

    assert not first() and second() and other_key()
