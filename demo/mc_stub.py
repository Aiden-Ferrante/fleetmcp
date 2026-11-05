"""A stand-in Mission Control, so the MCP server can be seen working without the
private apps it was built for.

    .venv/bin/python -m uvicorn demo.mc_stub:app --port 9090

It serves the nine routes the curated `mc_*` tools call, with the paths, methods
and query parameters of `fleetmcp/fixtures/mission-control.json` (the test
`tests/test_demo_stub.py` holds it to that). The process table is three fake
apps, held in memory:

    ollama        running   a seam: everything else needs it
    vaultwriter   running   needs ollama
    learnhub      stopped   needs ollama

The one rule it enforces is the real app's rule for its one destructive
endpoint, as the fixture describes it: `POST /api/apps/{id}/stop` is refused
without the token that `GET /api/apps/{id}/impact` issued for the blast radius
as it stands now. The impact is recomputed at stop time, so a dependent that
started after the read makes the token stale. Tokens are single-use.

The refusal sentences are this stub's own wording, written the way the fleet
apps write theirs: name the missing fact and the read that supplies it.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException


@dataclass
class _App:
    id: str
    port: int
    requires: list[str]
    running: bool


@dataclass
class _State:
    apps: dict[str, _App] = field(default_factory=dict)
    # token -> (app id, the dependents its impact read reported)
    issued: dict[str, tuple[str, list[str]]] = field(default_factory=dict)


def _seed() -> dict[str, _App]:
    return {a.id: a for a in (
        _App("ollama", 11434, [], True),
        _App("vaultwriter", 8020, ["ollama"], True),
        _App("learnhub", 8010, ["ollama"], False),
    )}


def create_app() -> FastAPI:
    """A fresh stub with its own state (tests make one each)."""
    st = _State(apps=_seed())
    api = FastAPI(title="Mission Control (demo stub)", version="0.1.0-stub")

    def get(app_id: str) -> _App:
        if app_id not in st.apps:
            raise HTTPException(404, f"no app '{app_id}' in the registry; mc_apps lists the ids")
        return st.apps[app_id]

    def dependents(app_id: str) -> list[str]:
        """Running apps that need `app_id`, directly or through another app."""
        out: set[str] = set()
        frontier = [app_id]
        while frontier:
            cur = frontier.pop()
            for a in st.apps.values():
                if cur in a.requires and a.running and a.id not in out:
                    out.add(a.id)
                    frontier.append(a.id)
        return sorted(out)

    def prereqs(app_id: str) -> list[str]:
        """Stopped prerequisites, in start order."""
        order: list[str] = []

        def visit(i: str) -> None:
            for r in st.apps[i].requires:
                visit(r)
                if not st.apps[r].running and r not in order:
                    order.append(r)

        visit(app_id)
        return order

    def row(a: _App) -> dict:
        return {"id": a.id, "status": "running" if a.running else "stopped", "port": a.port,
                "requires": a.requires}

    @api.get("/api/apps")
    def list_apps() -> list[dict]:
        return [row(a) for a in st.apps.values()]

    @api.get("/api/apps/{app_id}/impact")
    def app_impact(app_id: str) -> dict:
        get(app_id)
        deps = dependents(app_id)
        token = secrets.token_hex(8)
        st.issued[token] = (app_id, deps)
        return {"app": app_id, "dependents": deps, "start_order": prereqs(app_id), "token": token}

    @api.post("/api/apps/{app_id}/start")
    def start_app(app_id: str, with_deps: bool = False) -> dict:
        a = get(app_id)
        if a.running:
            return {"ok": True, "started": []}
        need = prereqs(app_id)
        if need and not with_deps:
            raise HTTPException(409, f"start refused: {app_id} needs {', '.join(need)} running first; "
                                     "pass with_deps=true to start them in order")
        for i in [*need, app_id]:
            st.apps[i].running = True
        return {"ok": True, "started": [*need, app_id]}

    @api.post("/api/apps/{app_id}/stop")
    def stop_app(app_id: str, token: str | None = None) -> dict:
        a = get(app_id)
        now = dependents(app_id)
        if token is None:
            raise HTTPException(428, f"stop refused: no impact token. Read mc_app_impact('{app_id}') "
                                     "and pass the token it returns, after the human says yes.")
        issued = st.issued.get(token)
        if issued is None or issued[0] != app_id:
            raise HTTPException(409, f"stop refused: that token was not issued for '{app_id}' "
                                     "(or was already used). Read mc_app_impact again.")
        then = issued[1]
        if then != now:
            raise HTTPException(409, f"stop refused: the impact token is stale. When it was read, stopping "
                                     f"{app_id} took down [{', '.join(then)}]; now it takes down "
                                     f"[{', '.join(now)}]. Read mc_app_impact('{app_id}') again and "
                                     "confirm the new blast radius with the human.")
        del st.issued[token]
        for i in [*now, app_id]:
            st.apps[i].running = False
        a.running = False
        return {"ok": True, "stopped": [*now, app_id]}

    @api.post("/api/apps/{app_id}/restart")
    def restart_app(app_id: str) -> dict:
        get(app_id)
        return {"ok": True, "restarted": app_id}

    @api.get("/api/jobs")
    def list_jobs() -> list[dict]:
        return []

    @api.get("/api/system")
    def system_stats() -> dict:
        return {"stub": True, "note": "the demo stub reports no host stats"}

    @api.get("/api/ollama")
    def ollama() -> dict:
        return {"daemons": []}

    @api.get("/api/health")
    def health() -> dict:
        return {"ok": True, "checks": {"registry": True}, "freshness": {}}

    return api


app = create_app()
