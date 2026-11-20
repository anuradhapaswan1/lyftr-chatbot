import pytest

from app import llm
from tests.conftest import EchoProvider, chat


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_live_then_cache_across_sessions(client):
    a = chat(client, "s-1", "What is the capital of France?").json()
    b = chat(client, "s-2", "What is the capital of France?").json()
    assert a["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert a["metadata"]["key_id"] == "key_1" and a["metadata"]["fallback_attempts"] == 0
    assert b["metadata"]["resolution_source"] == "LLM_CACHE"
    assert b["reply"] == a["reply"]
    assert EchoProvider.calls == 1  # provider hit exactly once
    assert b["metadata"]["key_id"] is None and b["metadata"]["fallback_attempts"] == 0
    assert b["metadata"]["input_tokens"] == a["metadata"]["input_tokens"]
    assert b["metadata"]["output_tokens"] == a["metadata"]["output_tokens"]


def test_different_message_is_not_cached(client):
    chat(client, "s-1", "alpha question")
    r = chat(client, "s-2", "beta question").json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_tokens_are_per_request_not_cumulative(client):
    first = chat(client, "s-1", "hello there friend").json()["metadata"]
    other = chat(client, "s-2", "hello there buddy").json()["metadata"]
    assert abs(first["input_tokens"] - other["input_tokens"]) <= 2
    follow = chat(client, "s-1", "and another one").json()["metadata"]
    assert follow["input_tokens"] > first["input_tokens"]  # carries history, not a sum of totals
    assert first["input_tokens"] > 0 and first["output_tokens"] > 0


def test_multi_turn_history_and_isolation(client):
    chat(client, "s-1", "my name is Ada")
    chat(client, "s-1", "what is my name?")
    chat(client, "s-2", "unrelated")
    msgs = client.get("/sessions/s-1/messages").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "my name is Ada"
    assert msgs[1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert "metadata" not in msgs[0]
    assert len(client.get("/sessions/s-2/messages").json()["messages"]) == 2


def test_history_is_sent_to_llm(client, monkeypatch):
    seen = []
    original = llm._raw_call

    async def spy(settings, key, messages, bypass):
        seen.append(messages)
        return await original(settings, key, messages, bypass)

    monkeypatch.setattr(llm, "_raw_call", spy)
    chat(client, "s-1", "remember 42", system_prompt="Be terse.")
    chat(client, "s-1", "what number?")
    assert seen[1][0] == {"role": "system", "content": "Be terse."}  # session prompt persists
    assert [m["content"] for m in seen[1][1:]][0] == "remember 42"
    assert seen[1][-1]["content"] == "what number?"


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


@pytest.mark.parametrize("body", [
    {"session_id": "s", "message": ""},
    {"session_id": "s", "message": "   "},
    {"session_id": "s"},
    {"message": "hi"},
    {"session_id": "", "message": "hi"},
    {"session_id": "s" * 129, "message": "hi"},
    {"session_id": "s", "message": "x" * 4001},
])
def test_validation_422(client, body):
    r = client.post("/chat", json=body)
    assert r.status_code == 422 and "detail" in r.json()


def test_malformed_json_422(client):
    r = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()


def test_failover_simulated_429_and_500(client):
    for i, h in enumerate(["key_1=429", "key_1=500"]):
        r = chat(client, f"s-{i}", f"hi {i}", {"X-Simulate-Failure": h})
        m = r.json()["metadata"]
        assert r.status_code == 200 and m["key_id"] == "key_2" and m["fallback_attempts"] == 1
        assert m["resolution_source"] == "LIVE_INFERENCE"


@pytest.mark.parametrize("header", ["*=429", "key_1=429,key_2=500", "*=503"])
def test_all_keys_fail_503_and_not_stored(client, header):
    r = chat(client, "s-x", "will fail", {"X-Simulate-Failure": header})
    assert r.status_code == 503 and r.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/s-x/messages").status_code == 404


def test_simulation_ignored_when_disabled(client, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    with TestClient(create_app()) as c:
        r = chat(c, "s", "hello", {"X-Simulate-Failure": "*=429"})
        assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_1"


def test_real_provider_failure_falls_over(client, monkeypatch):
    original = llm._raw_call

    async def flaky(settings, key, messages, bypass):
        if key == "fake-key-a":
            raise RuntimeError("429 quota exceeded")
        return await original(settings, key, messages, bypass)

    monkeypatch.setattr(llm, "_raw_call", flaky)
    m = chat(client, "s", "real failure").json()["metadata"]
    assert m["key_id"] == "key_2" and m["fallback_attempts"] == 1


def test_real_failure_everywhere_503(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("500")

    monkeypatch.setattr(llm, "_raw_call", boom)
    assert chat(client, "s", "x").status_code == 503


def test_parse_simulation():
    assert llm.parse_simulation("key_1=429, key_2=500") == {"key_1": 429, "key_2": 500}
    assert llm.parse_simulation("*=503") == {"*": 503}
    assert llm.parse_simulation("key_1=418,garbage") == {}
