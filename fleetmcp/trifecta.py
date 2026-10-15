"""Count the lethal-trifecta legs across the curated tools.

    python -m fleetmcp.trifecta

Reads the per-tool `trifecta` fields in tools.yaml (see the comment at its top)
and prints a per-app table and the totals. The counts are per tool server: the
agent session calling these tools usually also has a shell and web access, so
egress at the session level is not zero just because it is zero here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

LEGS = ("private_data", "untrusted_content", "egress")
TOOLS = Path(__file__).resolve().parent / "tools.yaml"


def load(path: Path = TOOLS) -> list[dict]:
    tools = yaml.safe_load(path.read_text())["tools"]
    for t in tools:
        tri = t.get("trifecta")
        if not isinstance(tri, dict) or set(tri) != set(LEGS) or not all(isinstance(tri[k], bool) for k in LEGS):
            raise ValueError(f"{t['name']}: trifecta must be {{{', '.join(LEGS)}}} booleans, got {tri!r}")
    return tools


def counts(tools: list[dict]) -> dict[str, int]:
    out = {"tools": len(tools)}
    for leg in LEGS:
        out[leg] = sum(t["trifecta"][leg] for t in tools)
    out["private_and_untrusted"] = sum(t["trifecta"]["private_data"] and t["trifecta"]["untrusted_content"] for t in tools)
    out["all_three"] = sum(all(t["trifecta"][k] for k in LEGS) for t in tools)
    return out


def main() -> int:
    tools = load()
    rows = [("app", "tools", "private data", "untrusted content", "egress")]
    for app in dict.fromkeys(t["app"] for t in tools):
        c = counts([t for t in tools if t["app"] == app])
        rows.append((app, c["tools"], c["private_data"], c["untrusted_content"], c["egress"]))
    c = counts(tools)
    rows.append(("total", c["tools"], c["private_data"], c["untrusted_content"], c["egress"]))
    w = [max(len(str(r[i])) for r in rows) for i in range(5)]
    for r in rows:
        print("  ".join(str(v).ljust(w[i]) if i == 0 else str(v).rjust(w[i]) for i, v in enumerate(r)))
    print(f"\nprivate AND untrusted in one tool: {c['private_and_untrusted']}; all three legs: {c['all_three']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
