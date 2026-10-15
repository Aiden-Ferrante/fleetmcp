"""The one HTTP path from the fleet MCP to the fleet apps.

Base URLs come from env (`FLEET_<APP>_URL`) with loopback defaults, because the
server runs on the box beside the apps: helm's auth exempts genuine loopback,
and shipyard, MC and learnhub bind loopback and have no auth. Every request
carries `X-Actor`, so the apps that record actors (shipyard, cycle B) know it
was an agent and which session — read from `.claude/fleet-session.json` under
the project dir, written by the SessionStart hook; `claude-code:unknown` when
that file is absent.

A non-2xx answer becomes a ToolError carrying the app's own `detail`: shipyard's
gates and the impact-token refusals are written to name the missing fact, and
the agent must see those words, not a status code.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

_CLIENT: httpx.Client | None = None
TIMEOUT = float(os.environ.get("FLEET_MCP_TIMEOUT", "20"))


class ToolError(RuntimeError):
    """An app refused or failed. `detail` is the app's own sentence."""

    def __init__(self, app: str, status: int, detail: str):
        super().__init__(f"{app} {status}: {detail}")
        self.app, self.status, self.detail = app, status, detail


def actor() -> str:
    """`claude-code:<session_id>` when a session file exists, else unknown."""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    f = Path(root) / ".claude" / "fleet-session.json"
    try:
        sid = json.loads(f.read_text()).get("session_id")
        if sid:
            return f"claude-code:{sid}"
    except (OSError, ValueError, AttributeError):
        pass
    return "claude-code:unknown"


def client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=TIMEOUT)
    return _CLIENT


def set_client(c: httpx.Client | None) -> None:
    """Tests inject a client over a MockTransport; None resets to the real one."""
    global _CLIENT
    _CLIENT = c


def call(base: str, app: str, method: str, path: str, *, params: dict | None = None,
         body: dict | None = None):
    params = {k: v for k, v in (params or {}).items() if v is not None}
    body = {k: v for k, v in (body or {}).items() if v is not None} if body is not None else None
    r = client().request(method, base.rstrip("/") + path, params=params or None,
                         json=body if body is not None and method in ("POST", "PUT", "PATCH") else None,
                         headers={"X-Actor": actor()})
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(app, r.status_code, str(detail))
    if not r.content:
        return {"ok": True}
    try:
        return r.json()
    except ValueError:
        return {"text": r.text}
