"""Read-only check of the viewer's API client against the real Honcho server.

Uses the URL and token the app saved in local/settings.json. Prints counts and
shapes only, never the token or message text.

    python tools/smoke_test.py           # every read the app uses, except /chat
    python tools/smoke_test.py --chat    # also asks one question (runs the server's LLM)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp1252 pipes choke on → and ·
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from honcho_viewer.client import CONCLUSION_LEVELS, HonchoClient, HonchoError  # noqa: E402
from honcho_viewer.store import LocalStore  # noqa: E402

failures = 0


def step(name: str, fn):
    global failures
    try:
        result = fn()
    except HonchoError as exc:
        failures += 1
        print(f"  FAIL {name}: {exc}")
        return None
    print(f"  ok   {name}")
    return result


def main() -> int:
    store = LocalStore(ROOT / "local")
    if not store.token:
        print("No token saved yet: open the app, paste the token, press Connect, then rerun.")
        return 2
    client = HonchoClient(store.base_url, store.token)
    print(f"Server: {store.base_url}")
    step("health", client.health)
    workspaces = step("list workspaces", client.list_workspaces) or []
    print(f"       {len(workspaces)} workspaces: {', '.join(w['id'] for w in workspaces)}")

    for w in workspaces:
        ws = w["id"]
        print(f"\n[{ws}]")
        peers = step("list peers", lambda: client.list_peers(ws)) or []
        print(f"       {len(peers)} peers: {', '.join(p['id'] for p in peers[:10])}")
        queue = step("queue status", lambda: client.queue_status(ws))
        if queue:
            print(f"       queue: {queue.get('pending_work_units')} waiting, "
                  f"{queue.get('in_progress_work_units')} processing")
        for p in peers[:3]:
            peer = p["id"]
            print(f"  peer {peer}")
            card = step("peer card", lambda: client.peer_card(ws, peer))
            rep = step("representation", lambda: client.representation(ws, peer))
            sessions = step("sessions", lambda: client.peer_sessions(ws, peer)) or []
            counts = step("conclusion counts", lambda: {
                lv: client.count_conclusions(ws, observed=peer, level=lv) for lv in CONCLUSION_LEVELS})
            print(f"       card facts {len(card or [])} · representation {len(rep or '')} chars · "
                  f"{len(sessions)} sessions · conclusions {counts}")
            if sessions:
                page = step("messages page", lambda: client.messages_page(ws, sessions[0]["id"]))
                if page:
                    print(f"       first session: {page.get('total')} messages")
            rows = step("list conclusions", lambda: client.list_conclusions(ws, observed=peer, max_items=100)) or []
            if rows:
                c = rows[0]
                step("get conclusion", lambda: client.get_conclusion(ws, c["id"]))
                found = step("search supporting messages", lambda: client.search_messages(
                    ws, c["content"], session_id=c.get("session_id"), peer_id=c.get("observed_id")))
                print(f"       sample conclusion level={c.get('level')} sources={len(c.get('source_ids') or [])} "
                      f"→ {len(found or [])} similar messages")
            if "--chat" in sys.argv and peers.index(p) == 0:
                resp = step("chat (minimal reasoning)", lambda: client.chat(ws, peer, {
                    "query": "In one sentence, what do you know about this peer?",
                    "reasoning_level": "minimal", "include_evidence": True}))
                if resp:
                    ev = resp.get("evidence") or {}
                    print(f"       answer {len(resp.get('content') or '')} chars in {resp['elapsed_s']} s · "
                          f"evidence: {len(ev.get('conclusions') or [])} conclusions, "
                          f"{len(ev.get('messages') or [])} messages, {len(ev.get('tool_calls') or [])} tool calls")

    print(f"\n{'ALL OK' if not failures else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
