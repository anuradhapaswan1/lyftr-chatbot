import litellm
import pytest
from fastapi.testclient import TestClient
from litellm import CustomLLM

from app.main import create_app


class EchoProvider(CustomLLM):
    """Offline provider routed through LiteLLM, so the real cache + usage path is exercised."""
    calls = 0

    async def acompletion(self, *args, **kwargs):
        EchoProvider.calls += 1
        messages = kwargs["messages"]
        last = messages[-1]["content"]
        text = f"Echo: {last}"
        prompt = sum(len(m["content"].split()) for m in messages)
        out = len(text.split())
        resp = litellm.ModelResponse()
        resp.choices[0].message.content = text
        resp.usage = litellm.Usage(prompt_tokens=prompt, completion_tokens=out,
                                   total_tokens=prompt + out)
        return resp


litellm.custom_provider_map = [{"provider": "echo", "custom_handler": EchoProvider()}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEYS", "fake-key-a,fake-key-b")
    monkeypatch.setenv("LLM_MODEL", "echo/test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("CACHE_TYPE", "local")
    EchoProvider.calls = 0
    with TestClient(create_app()) as c:  # fresh app, DB and cache per test
        yield c


def chat(client, session, message, headers=None, **extra):
    return client.post("/chat", json={"session_id": session, "message": message, **extra},
                       headers=headers or {})
