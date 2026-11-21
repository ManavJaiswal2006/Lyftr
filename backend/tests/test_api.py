import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import sleep

import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def provider():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = self.headers["Authorization"].removeprefix("Bearer ")
            messages = body["messages"]
            calls.append((key, messages))
            sleep(0.03)
            status = 200
            question = messages[-1]["content"]
            if question == "Force quota" and key == "first":
                status = 429
            elif question == "Force server error" and key == "first":
                status = 502
            elif question == "Both unavailable":
                status = 503
            content = {
                "id": "test-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": f"Reply to {question}"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": len(messages) * 7, "completion_tokens": 5, "total_tokens": len(messages) * 7 + 5},
            } if status == 200 else {
                "error": {"message": "Provider unavailable", "type": "insufficient_quota"},
            }
            result = json.dumps(content).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", calls
        finally:
            server.shutdown()
            worker.join()


@pytest.fixture
def client(monkeypatch, tmp_path, provider):
    base, calls = provider
    monkeypatch.setenv("LLM_API_BASE", base)
    monkeypatch.setenv("LLM_API_KEYS", "first,second")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "chat.db"))
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    with TestClient(create_app()) as http:
        yield http, calls


def send(http, session, message, *, simulation=None, **extras):
    return http.post(
        "/chat",
        json={"session_id": session, "message": message, **extras},
        headers={"X-Simulate-Failure": simulation} if simulation else {},
    )


def test_cache_context_tokens_and_persistence(client):
    http, calls = client
    first = send(http, "a", "  Hello  ").json()
    cached = send(http, "b", "Hello").json()
    assert first["reply"] == cached["reply"] == "Reply to Hello"
    live = first["metadata"]
    hit = cached["metadata"]
    assert live["resolution_source"] == "LIVE_INFERENCE"
    assert live["key_id"] == "key_1"
    assert hit["resolution_source"] == "LLM_CACHE"
    assert hit["key_id"] is None and hit["fallback_attempts"] == 0
    assert hit["latency_ms"] < live["latency_ms"]
    assert hit["input_tokens"] == live["input_tokens"] == 14
    assert hit["output_tokens"] == live["output_tokens"] == 5
    assert len(calls) == 1

    follow_up = send(http, "a", "Remember me?").json()
    assert follow_up["metadata"]["input_tokens"] == 28
    assert follow_up["metadata"]["output_tokens"] == 5
    assert [entry["content"] for entry in calls[-1][1][1:]] == ["Hello", "Reply to Hello", "Remember me?"]

    assert send(http, "c", "Different").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert send(http, "d", "Hello", system_prompt="Use a formal tone").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert send(http, "d", "Continue").status_code == 200
    assert calls[-1][1][0]["content"] == "Use a formal tone"

    with TestClient(create_app()) as restarted:
        history = restarted.get("/sessions/a/messages").json()
        assert history["session_id"] == "a"
        assert [item["role"] for item in history["messages"]] == ["user", "assistant", "user", "assistant"]
        assert history["messages"][1]["metadata"] == live
        assert restarted.get("/sessions/b/messages").json()["messages"][1]["metadata"] == hit


@pytest.mark.parametrize("question", ["Force quota", "Force server error"])
def test_actual_provider_errors_trigger_failover(client, question):
    http, calls = client
    response = send(http, "a", question)
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1
    assert [key for key, _ in calls] == ["first", "second"]


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_simulated_failure_bypasses_cache_and_is_request_scoped(client, status):
    http, calls = client
    assert send(http, "a", "Hello").status_code == 200
    response = send(http, "b", "Hello", simulation=f"key_1={status}")
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1
    assert [key for key, _ in calls] == ["first", "second"]
    assert send(http, "c", "New message").json()["metadata"]["key_id"] == "key_1"


def test_failed_requests_leave_history_untouched(client):
    http, calls = client
    assert send(http, "existing", "Hello").status_code == 200
    before = http.get("/sessions/existing/messages").json()

    for header in ("*=429", "key_1=429,key_2=500"):
        response = send(http, "failed", "Hello", simulation=header)
        assert response.status_code == 503
        assert response.json() == {"detail": "all providers unavailable"}
        assert http.get("/sessions/failed/messages").json() == {"detail": "session not found"}
    assert send(http, "existing", "Both unavailable").status_code == 503
    assert len(calls) == 3
    assert http.get("/sessions/existing/messages").json() == before


def test_health_validation_and_cors(client):
    http, calls = client
    assert http.get("/health").json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}
    for payload in ({}, {"message": "hello"}, {"session_id": ""}, {"session_id": "x" * 129, "message": "x"},
                    {"session_id": "a", "message": "   "}, {"session_id": "a", "message": "x" * 4001}):
        response = http.post("/chat", json=payload)
        assert response.status_code == 422
        assert "detail" in response.json()
    malformed = http.post("/chat", content="{", headers={"Content-Type": "application/json"})
    assert malformed.status_code == 422
    assert http.get("/sessions/a/messages").status_code == 404
    assert not calls
    preflight = http.options("/chat", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-Simulate-Failure",
    })
    assert preflight.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"


def test_simulation_disabled(client, monkeypatch):
    _, calls = client
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    with TestClient(create_app()) as http:
        response = send(http, "a", "Hello", simulation="*=429")
        assert response.status_code == 200
        assert response.json()["metadata"]["key_id"] == "key_1"
    assert len(calls) == 1
