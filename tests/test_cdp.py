import json
from pathlib import Path
import websocket

from codex_session_delete.cdp import BRIDGE_BINDING_NAME, _bridge_loop, build_bridge_script, inject_file, list_targets, pick_page_target


class TimeoutThenMessageSocket:
    def __init__(self):
        self.recv_count = 0
        self.sent = []

    def recv(self):
        self.recv_count += 1
        if self.recv_count == 1:
            raise websocket.WebSocketTimeoutException("idle")
        if self.recv_count == 2:
            return json.dumps({
                "method": "Runtime.bindingCalled",
                "params": {"payload": json.dumps({"id": "1", "path": "/diagnostic", "payload": {"session_id": "s1"}})},
            })
        raise RuntimeError("stop after response")

    def send(self, payload):
        self.sent.append(payload)


def test_pick_page_target_prefers_codex_title():
    targets = [
        {"type": "background_page", "title": "bg", "webSocketDebuggerUrl": "ws://bg"},
        {"type": "page", "title": "Codex", "url": "app://codex", "webSocketDebuggerUrl": "ws://page"},
    ]

    assert pick_page_target(targets)["webSocketDebuggerUrl"] == "ws://page"


def test_list_targets_bypasses_proxy_environment(monkeypatch):
    seen = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"type": "page"}]

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def get(self, url, timeout):
            seen["trust_env"] = self.trust_env
            seen["url"] = url
            seen["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr("codex_session_delete.cdp.requests.Session", FakeSession)

    assert list_targets(9229) == [{"type": "page"}]
    assert seen == {
        "trust_env": False,
        "url": "http://127.0.0.1:9229/json",
        "timeout": 3,
    }


def test_pick_page_target_rejects_missing_websocket():
    try:
        pick_page_target([{"type": "page", "title": "Codex"}])
    except RuntimeError as exc:
        assert "No injectable" in str(exc)
    else:
        raise AssertionError("target without websocket was accepted")


def test_build_bridge_script_installs_binding_callbacks():
    script = build_bridge_script("codexSessionDelete")

    assert "window.codexSessionDelete" in script
    assert "window.__codexSessionDeleteResolve" in script
    assert "window.__codexSessionDeleteReject" in script


def test_inject_file_prefix_exposes_binding_and_http_token(monkeypatch, tmp_path):
    script_path = tmp_path / "inject.js"
    script_path.write_text("console.log('ok')", encoding="utf-8")
    seen = {}

    monkeypatch.setattr("codex_session_delete.cdp.list_targets", lambda port: [{"type": "page", "title": "Codex", "url": "app://codex", "webSocketDebuggerUrl": "ws://page"}])
    monkeypatch.setattr("codex_session_delete.cdp.install_bridge", lambda websocket_url, binding_name, handler: {"bridge": True})

    def fake_evaluate_script(websocket_url, script):
        seen["websocket_url"] = websocket_url
        seen["script"] = script
        return {"result": {}}

    monkeypatch.setattr("codex_session_delete.cdp.evaluate_script", fake_evaluate_script)

    inject_file(9229, script_path, 57321, handler=lambda path, payload: {"status": "ok"}, http_mutation_token="token-1")

    assert seen["websocket_url"] == "ws://page"
    assert "window.__CODEX_SESSION_DELETE_HELPER__ = 'http://127.0.0.1:57321';" in seen["script"]
    assert 'window.__CODEX_SESSION_DELETE_BINDING__ = "codexSessionDeleteV2";' in seen["script"]
    assert 'window.__CODEX_SESSION_DELETE_HTTP_TOKEN__ = "token-1";' in seen["script"]


def test_bridge_binding_name_is_versioned_for_reinjection():
    assert BRIDGE_BINDING_NAME == "codexSessionDeleteV2"


def test_bridge_loop_continues_after_idle_timeout():
    ws = TimeoutThenMessageSocket()

    _bridge_loop(ws, lambda path, payload: {"status": "ok", "path": path})

    assert ws.recv_count == 3
    assert "__codexSessionDeleteResolve" in ws.sent[0]


def test_bridge_loop_supports_export_markdown_payload():
    ws = TimeoutThenMessageSocket()

    _bridge_loop(ws, lambda path, payload: {"status": "exported" if path == "/export-markdown" else "ok", "filename": "thread.md"})

    assert ws.recv_count == 3
    assert "__codexSessionDeleteResolve" in ws.sent[0]
