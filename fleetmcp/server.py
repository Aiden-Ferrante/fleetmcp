"""The fleet MCP server — one tool layer over shipyard, helm, Mission Control and
Learn Hub.

    .venv/bin/python -m fleetmcp.server        # stdio; point .mcp.json at this

Every tool is a generated function (fleetmcp/generated.py, from tools.yaml + the apps'
OpenAPI). This module only registers them, with the annotations the kind
implies: reads are read-only, destructive tools are marked destructive and take
`impact_token`. The words in each description are the protocol —
what the app refuses and what to read first — because a refusal an agent cannot
read is a wall, not a gate.

Design rules: one server, not one per app, so a
session in any repo sees the whole fleet; generated not hand-written; curated
not discovered; the server holds no state and makes no decision — it forwards,
and the apps refuse.
"""
from __future__ import annotations

from mcp.server import MCPServer
from mcp_types import ToolAnnotations

from . import generated

INSTRUCTIONS = (
    "The fleet's tool layer. shipyard owns the chart (problems, waypoints, anchors, "
    "cycles); helm is the task scheduler (agenda, plan, decisions); Mission Control "
    "supervises processes; Learn Hub holds courses and reviews. Before working in a "
    "fleet repo, read shipyard_focus and the repo's project; open work as a PBTR cycle "
    "(shipyard_open_cycle) and close it by cutting the tag and ticking review. A "
    "refusal names the missing fact — fix the fact, never route around it. "
    "Destructive tools need the impact_token their impact read returned, and a human's "
    "yes."
)

mcp = MCPServer("fleet", instructions=INSTRUCTIONS)

for t in generated.TOOLS:
    kind = t["kind"]
    mcp.add_tool(
        t["fn"],
        name=t["name"],
        annotations=ToolAnnotations(
            title=t["name"].replace("_", " "),
            read_only_hint=kind == "read",
            destructive_hint=kind == "destructive",
            idempotent_hint=kind == "read",
            open_world_hint=False,
        ),
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
