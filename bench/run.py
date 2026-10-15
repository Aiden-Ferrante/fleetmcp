#!/usr/bin/env python3
"""Own measurements: can a fresh Claude Code session work a PBTR cycle through the
fleet MCP alone?     BENCH_SHIPYARD=/path/to/shipyard .venv/bin/python bench/run.py --runs 3 --models default,haiku

Needs a shipyard checkout (the app the tools drive; not part of this repo) with
its backend venv at backend/.venv, and the Claude Code CLI (`claude` on PATH, or
BENCH_CLAUDE / --claude). Without those it cannot run; bench/results/ holds the
runs it produced.

Per run: a scratch shipyard on :8847 (fresh sqlite, its own CODE_ROOT), a tmp git repo with a bare "origin", one problem with a waypoint, then
`claude -p` with a fixed task, the fleet MCP as the ONLY MCP server, and a tool
allowlist. Scored by what is observable afterwards — a record in docs/cycles, a
commit, a tag, the tag on origin, review ticked on the chart — plus refusals hit,
tool calls and wall time. The refusal case seeds a waypointless problem: pass =
no cycle opened, nothing committed, and the final answer names the waypoint.

Every run is one sample; the table reports mean ± spread over the runs, and any
step below runs/runs is a finding, not something to smooth.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKTREE = Path(os.environ.get("BENCH_SHIPYARD", ""))  # set by --shipyard too; required
PORT = 8847
BASE = f"http://localhost:{PORT}"
CLAUDE = os.environ.get("BENCH_CLAUDE") or shutil.which("claude") or "claude"

TASK = """You are in a fleet repo. Use ONLY the fleet MCP tools (mcp__fleet__*) to talk to shipyard — never curl.
1. Call shipyard_direction for repo "{repo}" and read what it says.
2. If it would refuse, STOP: report the reason in one sentence and do nothing else.
3. Otherwise open a light PBTR cycle v0.1.0 on the problem it names with shipyard_open_cycle (title: "Bench line").
4. Append one line to README.md, then run: git add -A && git commit -m "feat: bench line" && git tag -a v0.1.0 -m v0.1.0 && git push origin HEAD --tags
5. Tick review on that problem with shipyard_tick_phases.
Report in two sentences what happened."""


def api(method: str, path: str, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def start_shipyard(tmp: Path, code_root: Path) -> subprocess.Popen:
    subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True)
    env = {**os.environ, "SHIPYARD_DB": str(tmp / "bench.sqlite"), "SHIPYARD_CHART_EXPORT": str(tmp / "chart.md"),
           "SHIPYARD_ARGUMENTS_DIR": str(tmp / "args"), "SHIPYARD_CODE_ROOT": str(code_root),
           "SHIPYARD_CYCLES_ROOT": str(tmp / "cycles")}
    p = subprocess.Popen([str(WORKTREE / "backend/.venv/bin/python"), "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
                          "--port", str(PORT)], cwd=WORKTREE, env=env, stdout=(tmp / "shipyard.log").open("w"), stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            api("GET", "/api/problems"); return p
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    raise SystemExit("scratch shipyard did not come up")


def git(repo: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True).stdout.strip()


def make_repo(code_root: Path, rid: str) -> Path:
    repo = code_root / rid; repo.mkdir(parents=True)
    (repo / "PROJECT.yaml").write_text(f"id: {rid}\nname: {rid}\nversion: 0.0.1\n")
    (repo / "README.md").write_text(f"# {rid}\n\nA scratch repo for the fleet bench.\n")
    (repo / "docs" / "cycles").mkdir(parents=True); (repo / "docs" / "cycles" / ".keep").write_text("")
    (repo / "Makefile").write_text("test:\n\t@true\n")
    # Registered like every real fleet repo — committed, so it is not "owed work" (v0.4.0 finding).
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"fleet": {
        "type": "stdio", "command": sys.executable, "args": ["-m", "fleetmcp.server"],
        "env": {"PYTHONPATH": str(ROOT), "FLEET_SHIPYARD_URL": BASE}}}}))
    bare = code_root / f"{rid}.git"; subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    for k, v in (("user.email", "bench@fleet"), ("user.name", "bench")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "chore: scratch"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "main"], check=True)
    return repo


def seed(rid: str, with_waypoint: bool) -> str:
    body = {"title": f"Bench problem {rid}", "waypoint": "README carries the bench line", "repo": rid}
    pid = api("POST", "/api/problems", body)["id"]
    if not with_waypoint:
        api("PATCH", f"/api/problems/{pid}", {"waypoint": ""})
    return pid


def run_claude(repo: Path, rid: str, model: str, tmp: Path, timeout: int) -> dict:
    cmd = [CLAUDE, "-p", TASK.format(repo=rid), "--output-format", "stream-json", "--verbose", "--strict-mcp-config",
           "--mcp-config", str(repo / ".mcp.json"), "--max-turns", "40",
           "--allowedTools", "mcp__fleet__*,Bash,Read,Edit,Write,Glob,Grep"]
    if model != "default":
        cmd += ["--model", model]
    env = {**os.environ, "FLEET_SHIPYARD_URL": BASE}
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout)
        out, timed_out = r.stdout, False
    except subprocess.TimeoutExpired as e:
        out, timed_out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), True
    wall = time.time() - t0
    parsed = parse(out)
    (tmp / "transcript.jsonl").write_text(out)
    return {"wall_s": round(wall, 1), "timed_out": timed_out, **parsed}


def parse(out: str) -> dict:
    """Count tool calls and refusals from a stream-json transcript. A refusal is a
    tool_result flagged is_error, or one whose text carries a gate's "refused" sentence
    — excluding shipyard_direction, whose payload *describes* refusals without being one."""
    names: dict[str, str] = {}
    tool_calls = refusals = 0; final = ""; cost = None; turns = None
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tool_calls += 1; names[b.get("id")] = b.get("name", "")
        elif ev.get("type") == "user":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") != "tool_result":
                    continue
                text = json.dumps(b.get("content", "")).lower()
                if names.get(b.get("tool_use_id"), "").endswith("shipyard_direction"):
                    continue
                if b.get("is_error") or "refused" in text:
                    refusals += 1
        elif ev.get("type") == "result":
            final = ev.get("result") or ""; cost = ev.get("total_cost_usd"); turns = ev.get("num_turns")
    return {"tool_calls": tool_calls, "refusals": refusals, "cost_usd": cost, "turns": turns, "final": final[:600]}


def rescore(path: Path) -> None:
    """Re-parse every row's saved transcript with the current parser and rewrite the table."""
    data = json.loads(path.read_text())
    for r in data["rows"]:
        t = Path(r["tmp"]) / "transcript.jsonl"
        if t.exists():
            r.update(parse(t.read_text()))
    path.write_text(json.dumps(data, indent=1)); print(table(data["rows"], 0))


def score(repo: Path, code_root: Path, rid: str, pid: str) -> dict:
    node = api("GET", f"/api/chart/{pid}")["node"]
    records = [p.name for p in (repo / "docs" / "cycles").glob("v*.md")]
    return {"cycle_opened": bool(records), "commit": git(repo, "rev-list", "--count", "HEAD") not in ("", "1"),
            "tag": git(repo, "tag", "-l", "v0.1.0") == "v0.1.0",
            "pushed": git(code_root / f"{rid}.git", "tag", "-l", "v0.1.0") == "v0.1.0",
            "review_ticked": bool(node.get("phase_review")), "records": records}


def one(kind: str, model: str, n: int, timeout: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"fleetbench-{kind}-{model}-{n}-")); code_root = tmp / "code"
    rid = f"bench-{n}"
    proc = start_shipyard(tmp, code_root)
    try:
        repo = make_repo(code_root, rid)
        pid = seed(rid, with_waypoint=(kind == "happy"))
        res = run_claude(repo, rid, model, tmp, timeout)
        sc = score(repo, code_root, rid, pid)
        if kind == "happy":
            sc["pass"] = all(sc[k] for k in ("cycle_opened", "commit", "tag", "pushed", "review_ticked"))
        else:
            sc["pass"] = (not sc["cycle_opened"] and not sc["commit"] and "waypoint" in res["final"].lower())
        return {"kind": kind, "model": model, "run": n, "tmp": str(tmp), **res, **sc}
    finally:
        proc.terminate()


def table(rows: list[dict], runs: int) -> str:
    out = ["| case | model | pass | cycle | commit | tag | pushed | review | refusals | tool calls | wall s |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for kind in ("happy", "refusal"):
        for model in sorted({r["model"] for r in rows}):
            rs = [r for r in rows if r["kind"] == kind and r["model"] == model]
            if not rs: continue
            def cnt(k): return f"{sum(1 for r in rs if r[k])}/{len(rs)}"
            def ms(k):
                xs = [r[k] for r in rs if r[k] is not None]
                return f"{statistics.mean(xs):.0f} ± {(statistics.pstdev(xs) if len(xs) > 1 else 0):.0f}" if xs else "—"
            out.append(f"| {kind} | {model} | {cnt('pass')} | {cnt('cycle_opened')} | {cnt('commit')} | {cnt('tag')} | {cnt('pushed')} | {cnt('review_ticked')} | {ms('refusals')} | {ms('tool_calls')} | {ms('wall_s')} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3); ap.add_argument("--models", default="default,haiku")
    ap.add_argument("--kinds", default="happy,refusal"); ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--out", default=str(ROOT / "bench" / "results" / f"{time.strftime('%Y-%m-%d')}.json"))
    ap.add_argument("--shipyard", help="shipyard checkout to run (or BENCH_SHIPYARD)")
    ap.add_argument("--claude", help="Claude Code CLI binary (or BENCH_CLAUDE; default: claude on PATH)")
    ap.add_argument("--rescore", help="re-parse the transcripts behind an existing results file")
    a = ap.parse_args()
    if a.rescore:
        rescore(Path(a.rescore)); return 0
    global WORKTREE, CLAUDE
    WORKTREE = Path(a.shipyard) if a.shipyard else WORKTREE
    CLAUDE = a.claude or CLAUDE
    if not str(WORKTREE) or str(WORKTREE) == "." or not (WORKTREE / "backend").is_dir():
        raise SystemExit("set --shipyard or BENCH_SHIPYARD to a shipyard checkout (it has backend/)")
    rows = []
    for kind in a.kinds.split(","):
        for model in a.models.split(","):
            for n in range(1, a.runs + 1):
                r = one(kind, model, n, a.timeout); rows.append(r)
                print(f"[{kind} {model} #{n}] pass={r['pass']} cycle={r['cycle_opened']} commit={r['commit']} tag={r['tag']} "
                      f"pushed={r['pushed']} review={r['review_ticked']} refusals={r['refusals']} calls={r['tool_calls']} "
                      f"wall={r['wall_s']}s timeout={r['timed_out']}\n    {r['final'][:200]!r}", flush=True)
                Path(a.out).write_text(json.dumps({"task": TASK, "rows": rows}, indent=1))
    print(); print(table(rows, a.runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
