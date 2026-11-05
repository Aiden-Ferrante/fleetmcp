"""The demo's Mission Control stub: it matches the checked-in OpenAPI fixture for
every curated mc_* tool, and it enforces the impact-token rule on stop — and that
refusal reaches an MCP caller as the stub's own sentence.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from demo.mc_stub import create_app
from fleetmcp import generated, http
from fleetmcp.server import mcp

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "fleetmcp" / "fixtures" / "mission-control.json").read_text())
MC_TOOLS = [t for t in yaml.safe_load((ROOT / "fleetmcp" / "tools.yaml").read_text())["tools"]
            if t["app"] == "mission-control"]


@pytest.fixture
def mc() -> TestClient:
    return TestClient(create_app())


def _query_params(op: dict) -> set[str]:
    return {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}


def test_stub_serves_every_curated_mc_route_with_the_fixtures_query_params(mc):
    stub = mc.get("/openapi.json").json()["paths"]
    for t in MC_TOOLS:
        m = t["method"].lower()
        assert m in stub.get(t["path"], {}), f"{t['name']}: stub lacks {t['method']} {t['path']}"
        assert _query_params(stub[t["path"]][m]) == _query_params(FIXTURE["paths"][t["path"]][m]), t["name"]


def test_stop_without_a_token_is_refused(mc):
    r = mc.post("/api/apps/ollama/stop")
    assert r.status_code == 428
    assert "no impact token" in r.json()["detail"]
    assert mc.get("/api/apps").json()[0]["status"] == "running"


def test_stop_with_a_fresh_token_takes_the_dependents_down(mc):
    tok = mc.get("/api/apps/ollama/impact").json()
    assert tok["dependents"] == ["vaultwriter"]
    r = mc.post("/api/apps/ollama/stop", params={"token": tok["token"]})
    assert r.status_code == 200 and r.json()["stopped"] == ["vaultwriter", "ollama"]
    assert {a["id"]: a["status"] for a in mc.get("/api/apps").json()} == {
        "ollama": "stopped", "vaultwriter": "stopped", "learnhub": "stopped"}


def test_a_dependent_started_after_the_read_makes_the_token_stale(mc):
    tok = mc.get("/api/apps/ollama/impact").json()["token"]
    assert mc.post("/api/apps/learnhub/start").status_code == 200
    r = mc.post("/api/apps/ollama/stop", params={"token": tok})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert "stale" in d and "[vaultwriter]" in d and "[learnhub, vaultwriter]" in d
    assert mc.get("/api/apps").json()[0]["status"] == "running"


def test_tokens_are_single_use_and_bound_to_their_app(mc):
    tok = mc.get("/api/apps/vaultwriter/impact").json()["token"]
    assert mc.post("/api/apps/ollama/stop", params={"token": tok}).status_code == 409
    assert mc.post("/api/apps/vaultwriter/stop", params={"token": tok}).status_code == 200
    assert mc.post("/api/apps/vaultwriter/start").status_code == 200
    again = mc.post("/api/apps/vaultwriter/stop", params={"token": tok})
    assert again.status_code == 409 and "already used" in again.json()["detail"]


def test_start_refuses_missing_prerequisites_unless_with_deps(mc):
    tok = mc.get("/api/apps/ollama/impact").json()["token"]
    mc.post("/api/apps/ollama/stop", params={"token": tok})
    r = mc.post("/api/apps/learnhub/start")
    assert r.status_code == 409 and "needs ollama" in r.json()["detail"]
    r = mc.post("/api/apps/learnhub/start", params={"with_deps": True})
    assert r.json()["started"] == ["ollama", "learnhub"]


def test_unknown_app_is_a_404(mc):
    assert mc.get("/api/apps/nope/impact").status_code == 404


def test_the_stale_token_refusal_reaches_the_mcp_caller_verbatim(mc):
    """Through the generated tools and the MCP server, against the stub: the agent
    gets the stub's sentence, not a status code."""
    from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
    http.set_client(mc)
    try:
        tok = generated.mc_app_impact(app_id="ollama")["token"]
        generated.mc_start_app(app_id="learnhub")
        with pytest.raises(SdkToolError) as e:
            asyncio.run(mcp.call_tool("mc_stop_app", {"app_id": "ollama", "impact_token": tok}))
        assert "the impact token is stale" in str(e.value)
    finally:
        http.set_client(None)


def test_the_demo_runs_end_to_end_over_stdio():
    """`make demo` itself: a real stub process, the MCP server as a stdio subprocess.
    The steps the README quotes from demo/transcript.txt must still come out."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "demo.run_demo"], cwd=ROOT, capture_output=True, text=True,
                       timeout=60, check=True)
    out = r.stdout
    assert "# fleetmcp over stdio: 50 tools" in out
    assert "required=['app_id', 'impact_token']" in out
    assert out.count("<- isError=True") == 1
    assert "mission-control 409: stop refused: the impact token is stale." in out
    assert '"stopped": ["learnhub", "vaultwriter", "ollama"]' in out
