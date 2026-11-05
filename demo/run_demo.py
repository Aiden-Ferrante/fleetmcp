"""Start the Mission Control stub, start the fleet MCP server over stdio, and call
its tools the way an agent would: a normal read, then a destructive call that the
app refuses, then the same call done right.

    make demo                       # or: .venv/bin/python -m demo.run_demo
    make transcript                 # re-record demo/transcript.txt

Nothing here is mocked between the client and the stub: the MCP client spawns
`python -m fleetmcp.server` as a subprocess, which makes real HTTP calls to the
stub on a free loopback port. What is printed is what came back; the
`HTTP Request:` lines are the server subprocess's own httpx log on stderr.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_stub(port: int) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "demo.mc_stub:app", "--port", str(port),
                          "--log-level", "warning"], cwd=ROOT)
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return p
        except httpx.HTTPError:
            time.sleep(0.1)
    p.terminate()
    raise SystemExit("the Mission Control stub did not come up")


def _blocks(result) -> list[str]:
    """Each content block's text, JSON compacted to one line (a list comes back as one block per item)."""
    out = []
    for c in result.content:
        text = getattr(c, "text", str(c))
        try:
            out.append(json.dumps(json.loads(text)))
        except ValueError:
            out.append(text)
    return out


async def drive(base: str) -> None:
    server = StdioServerParameters(command=sys.executable, args=["-m", "fleetmcp.server"], cwd=str(ROOT),
                                   env={"FLEET_MC_URL": base, "PYTHONPATH": str(ROOT)})
    async with Client(stdio_client(server)) as c:
        tools = (await c.list_tools()).tools
        stop = next(t for t in tools if t.name == "mc_stop_app")
        print(f"# fleetmcp over stdio: {len(tools)} tools; Mission Control stub at {base}")
        hint = stop.annotations.destructive_hint if stop.annotations else None
        print(f"# mc_stop_app: destructiveHint={hint}, "
              f"required={stop.input_schema.get('required')}", flush=True)

        async def call(step: str, name: str, args: dict) -> str:
            print(f"\n## {step}")
            print(f"-> {name} {json.dumps(args)}", flush=True)
            r = await c.call_tool(name, args)
            blocks = _blocks(r)
            print(f"<- isError={bool(r.is_error)}", flush=True)
            for b in blocks:
                print(f"   {b}", flush=True)
            return blocks[0]

        await call("1. normal read", "mc_apps", {})
        impact = json.loads(await call("2. read the blast radius before stopping ollama",
                                       "mc_app_impact", {"app_id": "ollama"}))
        await call("3. meanwhile, a dependent starts", "mc_start_app", {"app_id": "learnhub"})
        await call("4. destructive call with the now-stale token: the app refuses",
                   "mc_stop_app", {"app_id": "ollama", "impact_token": impact["token"]})
        fresh = json.loads(await call("5. re-read the impact", "mc_app_impact", {"app_id": "ollama"}))
        await call("6. same call with the fresh token (in real use, after the human says yes)",
                   "mc_stop_app", {"app_id": "ollama", "impact_token": fresh["token"]})


def main() -> int:
    port = _free_port()
    stub = start_stub(port)
    try:
        asyncio.run(drive(f"http://127.0.0.1:{port}"))
    finally:
        stub.terminate()
        stub.wait(timeout=10)
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
