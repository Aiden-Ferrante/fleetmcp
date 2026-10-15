"""The fleet MCP: generated code is current, every curated tool is registered
with the annotations its kind implies, and a call reaches the app and returns
the app's words — including its refusal.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from fleetmcp import gen, http
from fleetmcp import generated
from fleetmcp.server import mcp

ROOT = Path(__file__).resolve().parents[1]
TOOLS = yaml.safe_load((ROOT / "fleetmcp" / "tools.yaml").read_text())["tools"]


def test_generated_is_current():
    """Stale generated.py is the failure this file exists to catch: a schema or
    tools.yaml edit with no re-render means the server lies about a signature."""
    assert (ROOT / "fleetmcp" / "generated.py").read_text() == gen.render()


def test_every_curated_tool_is_registered_once():
    names = [t.name for t in asyncio.run(mcp.list_tools())]
    assert sorted(names) == sorted(t["name"] for t in TOOLS)
    assert len(names) == len(set(names))


def test_annotations_follow_kind():
    by = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for t in TOOLS:
        a = by[t["name"]].annotations
        assert a.read_only_hint is (t["kind"] == "read"), t["name"]
        assert a.destructive_hint is (t["kind"] == "destructive"), t["name"]


def test_destructive_tools_require_an_impact_token():
    by = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for t in TOOLS:
        req = by[t["name"]].input_schema.get("required", [])
        assert ("impact_token" in req) is (t["kind"] == "destructive"), t["name"]


def test_every_tool_path_exists_in_its_fixture():
    for t in TOOLS:
        spec = json.loads((ROOT / "fleetmcp" / "fixtures" / f"{t['app']}.json").read_text())
        assert t["method"].lower() in spec["paths"][t["path"]], t["name"]


@pytest.fixture
def stub():
    """A stand-in for every app: records the request, answers what the test set."""
    seen: list[httpx.Request] = []
    state = {"status": 200, "body": {"ok": True}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(state["status"], json=state["body"])

    http.set_client(httpx.Client(transport=httpx.MockTransport(handler)))
    yield seen, state
    http.set_client(None)


def test_a_read_reaches_the_app_with_the_actor_header(stub, monkeypatch, tmp_path):
    seen, state = stub
    state["body"] = {"slots": []}
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "fleet-session.json").write_text(json.dumps({"session_id": "abc123"}))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = generated.shipyard_focus()
    assert out == {"slots": []}
    assert seen[0].method == "GET" and seen[0].url.path == "/api/focus"
    assert seen[0].headers["X-Actor"] == "claude-code:abc123"


def test_a_write_sends_the_body_and_drops_nulls(stub):
    seen, _ = stub
    generated.shipyard_open_cycle(pid="p1", version="v0.1.0", title="t")
    r = seen[0]
    assert r.method == "POST" and r.url.path == "/api/problems/p1/cycles/open"
    assert json.loads(r.content) == {"version": "v0.1.0", "title": "t"}


def test_a_destructive_tool_sends_the_token_as_a_query(stub):
    seen, _ = stub
    generated.shipyard_thaw(pid="p1", impact_token="tok")
    assert seen[0].method == "DELETE" and seen[0].url.params["token"] == "tok"


def test_a_refusal_carries_the_apps_own_words(stub):
    _, state = stub
    state["status"], state["body"] = 422, {"detail": "open refused: 'x' has no waypoint"}
    with pytest.raises(http.ToolError) as e:
        generated.shipyard_open_cycle(pid="p1", version="v0.1.0", title="t")
    assert "has no waypoint" in str(e.value) and e.value.status == 422


def test_call_tool_through_the_server_surfaces_the_refusal(stub):
    """Over the wire the SDK turns this into CallToolResult(isError=True) whose text
    is exactly the app's sentence — the agent reads the gate, not a status code."""
    from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
    _, state = stub
    state["status"], state["body"] = 422, {"detail": "review refused: helm owes uncommitted"}
    with pytest.raises(SdkToolError) as e:
        asyncio.run(mcp.call_tool("shipyard_tick_phases", {"pid": "p1", "review": True}))
    assert "review refused: helm owes uncommitted" in str(e.value)


def test_no_generated_call_repeats_a_key():
    """The v0.1 bug: destructive tools rendered params={'token': token, 'token': impact_token}.
    Python keeps the last value, so one argument was silently dropped and no other test
    noticed. Any dict literal in generated.py with a repeated key is that bug again."""
    import ast
    tree = ast.parse((ROOT / "fleetmcp" / "generated.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            assert len(keys) == len(set(keys)), f"line {node.lineno}: repeated key in {keys}"


def test_destructive_tools_take_one_token_and_send_it_once(stub):
    """impact_token is the only token argument, and it lands as the app's ?token=."""
    import inspect
    seen, _ = stub
    for t in TOOLS:
        if t["kind"] != "destructive":
            continue
        fn = getattr(generated, t["name"])
        params = inspect.signature(fn).parameters
        assert "token" not in params, t["name"]
        path_args = {p: "x" for p in params if p not in ("impact_token",) and params[p].default is inspect._empty}
        fn(**path_args, impact_token="tok-" + t["name"])
        assert seen[-1].url.params.get_list("token") == ["tok-" + t["name"]], t["name"]


def test_the_session_file_is_optional(stub, monkeypatch, tmp_path):
    """Outside a hooked Claude Code session there is no session file; calls still work."""
    seen, _ = stub
    monkeypatch.delenv("FLEETMCP_SESSION_FILE", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    generated.shipyard_focus()
    assert seen[0].headers["X-Actor"] == "claude-code:unknown"
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"session_id": "xyz"}))
    monkeypatch.setenv("FLEETMCP_SESSION_FILE", str(f))
    generated.shipyard_focus()
    assert seen[1].headers["X-Actor"] == "claude-code:xyz"
