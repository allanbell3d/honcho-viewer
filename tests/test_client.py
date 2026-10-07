import pytest

from honcho_viewer.client import HonchoClient, HonchoError


def test_health_works_without_token(fake):
    assert HonchoClient(fake.url, "").health() == {"status": "ok"}


def test_list_workspaces_sends_bearer_token(client, fake):
    ids = [w["id"] for w in client.list_workspaces()]

    assert ids == ["test-qwen", "test-llama", "big"]
    assert fake.last("/workspaces/list")["auth"] == "Bearer test-token"


def test_rejected_token_raises_401_with_server_detail(fake):
    with pytest.raises(HonchoError) as err:
        HonchoClient(fake.url, "wrong").list_workspaces()

    assert err.value.status == 401
    assert "Invalid access token" in err.value.detail


def test_unreachable_server_raises_status_0():
    with pytest.raises(HonchoError) as err:
        HonchoClient("http://127.0.0.1:9", "t", timeout=2).health()

    assert err.value.status == 0
    assert "Cannot reach" in err.value.detail


def test_validation_error_detail_is_readable(client):
    with pytest.raises(HonchoError) as err:
        client._request("POST", "/v3/workspaces/{ws}/conclusions/list", {"ws": "big"},
                        body={"filters": {"bogus": 1}})

    assert err.value.status == 422
    assert "bad filter" in err.value.detail


def test_list_conclusions_follows_all_pages(client, fake):
    rows = client.list_conclusions("big")

    assert len(rows) == 131
    assert [r["query"]["page"] for r in fake.requests if r["path"].endswith("conclusions/list")] == ["1", "2"]


def test_list_conclusions_sends_only_given_filters(client, fake):
    rows = client.list_conclusions("test-qwen", observed="alice", level="deductive")

    assert fake.last("conclusions/list")["body"] == {"filters": {"observed_id": "alice", "level": "deductive"}}
    assert {r["level"] for r in rows} == {"deductive"}


def test_list_conclusions_without_filters_sends_none(client, fake):
    client.list_conclusions("test-qwen")

    assert fake.last("conclusions/list")["body"] == {"filters": None}


def test_count_conclusions_reads_total_from_one_item_page(client, fake):
    assert client.count_conclusions("big", observed="alice") == 131
    assert fake.last("conclusions/list")["query"]["size"] == "1"


def test_ids_with_special_characters_are_path_encoded(client, fake):
    card = client.peer_card("test-qwen", "user@home/x")

    req = fake.last("/card")
    assert req["segments"][4] == "user@home/x"
    assert card[0] == "user@home/x lives in Lisbon"


def test_peer_card_passes_target_as_query(client, fake):
    client.peer_card("test-qwen", "agent", target="alice")

    assert fake.last("/card")["query"] == {"target": "alice"}


def test_representation_returns_text(client):
    assert client.representation("test-llama", "alice") == "llama3-70b representation of alice"


def test_representation_asks_for_the_server_maximum(client, fake):
    # Without max_conclusions the server returns only its 25 most recent conclusions.
    client.representation("test-llama", "alice")
    assert fake.last("/representation")["body"]["max_conclusions"] == 100


def test_conclusion_list_is_not_capped_by_default():
    import inspect

    from honcho_viewer.client import HonchoClient
    assert inspect.signature(HonchoClient.list_conclusions).parameters["max_items"].default is None


def test_peer_sessions_and_message_pages(client):
    sessions = client.peer_sessions("test-qwen", "agent")
    page = client.messages_page("test-qwen", "s2", page=2, size=50)

    assert [s["id"] for s in sessions] == ["s1"]
    assert page["total"] == 120 and page["page"] == 2 and len(page["items"]) == 50


def test_chat_sends_options_and_measures_time(client, fake):
    body = {"query": "where does alice live?", "reasoning_level": "medium", "include_evidence": True}
    resp = client.chat("test-qwen", "alice", body)

    assert fake.last("/chat")["body"] == body
    assert resp["content"] == "[qwen3-32b] answer to: where does alice live?"
    assert resp["evidence"]["tool_calls"][0]["tool_name"] == "search_memory"
    assert resp["elapsed_s"] >= 0


def test_search_messages_scopes_by_session_and_peer(client, fake):
    rows = client.search_messages("test-qwen", "sailing", session_id="s1", peer_id="alice")

    assert fake.last("/search")["body"] == {"query": "sailing", "limit": 5,
                                             "filters": {"session_id": "s1", "peer_id": "alice"}}
    assert [m["content"] for m in rows] == ["I live in Lisbon and I love <b>sailing</b>."]


def test_get_conclusion_by_id(client):
    assert client.get_conclusion("test-qwen", "test-qwen-c0")["level"] == "explicit"


def test_queue_status(client):
    assert client.queue_status("test-qwen")["in_progress_work_units"] == 1


def test_refuses_routes_outside_allowlist_without_network(client, fake):
    with pytest.raises(ValueError, match="not allowlisted"):
        client._request("POST", "/v3/workspaces", body={"id": "oops"})

    assert fake.requests == []


def test_trailing_slash_in_base_url_is_ignored(fake):
    assert HonchoClient(fake.url + "/", "x").health() == {"status": "ok"}
