"""Integration tests for the HTTP API (fake LLM, real graph, real database)."""

import json
import uuid

from fastapi.testclient import TestClient

from app import graph, llm, main
from tests.test_graph import FakeLLM, draft, intent

client = TestClient(main.app)


def setup_function():
    graph._graph = None
    main._hits.clear()


def test_health_and_schema():
    assert client.get("/api/health").json()["status"] == "ok"
    s = client.get("/api/schema").json()
    assert "Employees" in s["tables"] and s["examples"]


def test_chat_endpoint_returns_sql_and_results():
    llm.set_client(FakeLLM(intents=[intent("generate")], drafts=[draft("SELECT COUNT(*) AS n FROM Customers")]))
    r = client.post("/api/chat", json={"session_id": "api-" + uuid.uuid4().hex[:8], "message": "how many customers?"})
    body = r.json()
    assert r.status_code == 200 and body["type"] == "sql"
    assert body["results"]["rows"][0][0] == 300


def test_stream_endpoint_emits_steps_then_result():
    llm.set_client(FakeLLM(intents=[intent("out_of_scope")]))
    r = client.post("/api/chat/stream", json={"session_id": "st-1", "message": "who won the world cup"})
    events = [e for e in r.text.split("\n\n") if e.strip()]
    kinds = [e.split("\n")[0] for e in events]
    assert kinds[0] == "event: step" and kinds[-1] == "event: result"
    result = json.loads(events[-1].split("data: ", 1)[1])
    assert result["type"] == "refusal"


def test_rejects_bad_session_id():
    r = client.post("/api/chat", json={"session_id": "bad id!", "message": "hi"})
    assert r.status_code == 400


def test_rate_limit():
    llm.set_client(FakeLLM(intents=[intent("out_of_scope")] * 100))
    codes = [client.post("/api/chat", json={"session_id": "rl", "message": "x"}).status_code
             for _ in range(main.config.RATE_LIMIT_REQUESTS + 1)]
    assert codes[-1] == 429


def test_llm_failure_returns_friendly_error():
    class Broken:
        def structured(self, *a, **k):
            raise RuntimeError("Error code: 401 - authentication_error")
    llm.set_client(Broken())
    body = client.post("/api/chat", json={"session_id": "err", "message": "show customers"}).json()
    assert body["type"] == "error" and "API key" in body["message"]
