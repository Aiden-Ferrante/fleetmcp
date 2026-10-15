# fleetmcp

One MCP server, generated from OpenAPI specs, that gives a coding agent 50 curated tools over four local apps, and forwards their refusals instead of making decisions itself.

**Result:** in 12 headless Claude Code runs (6 normal tasks, 6 refusal cases, two models), every run passed: each normal task opened a cycle, committed, tagged, pushed and ticked review using only these tools, and each refusal case stopped with nothing opened or committed. Total cost was $2.29 (`bench/results/2026-09-05.json`).

## Quickstart

Needs Python 3.12 (the version tested). No app has to be running: the tests use `httpx.MockTransport` and the checked-in OpenAPI fixtures.

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q          # 16 tests, offline
.venv/bin/python -m fleetmcp.gen             # re-render generated.py from tools.yaml + fixtures/
.venv/bin/python -m fleetmcp.trifecta        # private-data / untrusted-content / egress counts
```

## What went wrong / what I learned

- **A generator bug silently dropped an argument.** The three destructive endpoints already declare `?token=`, and the generator appended the impact token on top. The output was `params={'token': token, 'token': impact_token}`. Python keeps the last key, so one argument was accepted and thrown away, and the ten existing tests never noticed. It is fixed in `gen.py`. `tests/test_mcp.py` now rejects any repeated key in `generated.py` and checks that each destructive tool sends exactly one token. Both checks fail on the old file.
- **The guardrails live in the apps, not here.** This server holds no state and decides nothing. A destructive call goes through only with a token from the matching impact read, and the app (shipyard or Mission Control) checks the token against the impact as it stands now and refuses a missing or stale one. The server's job is to carry the app's own refusal sentence back to the agent, so the agent sees the reason rather than a bare status code.
- **Refusal cases are in the evals.** Half the bench seeds a problem the gates must refuse. A run passes only if nothing was opened or committed and the agent's answer names the missing fact. In the first bench pass, the default model failed 3/3 normal tasks. The cause was the harness: the scratch repo itself had uncommitted files, and the model correctly stopped at the review gate (`bench/results/2026-09-05-pass1-dirty-harness.*`). The harness was fixed and the bench re-run.
- **My first trifecta count was wrong.** I had classified tools by group and got 47 private / 25 untrusted / 0 egress. Classifying each tool by reading its handler gives 37 / 37 / 0. Many writes return rows that other sessions wrote, and health and ack-only endpoints carry no owner data. The counts are per tool server. The agent calling these tools usually also has a shell and web access, so at the session level all three legs can be present.

## Layout

- `fleetmcp/tools.yaml` is the curated tool list: app, method, path, kind (read / write / destructive), the description the agent reads, and the trifecta classification with one line of evidence per tool. It has 31 read, 16 write and 3 destructive tools.
- `fleetmcp/gen.py` renders `fleetmcp/generated.py` from `tools.yaml` and `fleetmcp/fixtures/<app>.json` (each app's OpenAPI spec). `--check` exits 1 when the generated file is stale. `--fetch` refreshes the fixtures from running apps.
- `fleetmcp/server.py` registers each generated function with MCP annotations derived from its kind: `readOnlyHint` for reads, and `destructiveHint` plus a required `impact_token` for destructive tools. Run it with `python -m fleetmcp.server` (stdio).
- `fleetmcp/http.py` is the single HTTP path. App base URLs come from `FLEET_<APP>_URL` and default to loopback. Every request carries an `X-Actor` header. The session id in that header is optional and is read from `$FLEETMCP_SESSION_FILE`, or from `.claude/fleet-session.json` when that file exists.
- `fleetmcp/trifecta.py` loads and validates the per-tool trifecta fields and prints the counts.
- `bench/run.py` is the end-to-end eval. It needs a shipyard checkout (`--shipyard` or `BENCH_SHIPYARD`) and the Claude Code CLI on PATH, so it cannot run from this repo alone. `bench/results/` holds the runs it produced.

The four apps it drives (shipyard, helm, Mission Control, Learn Hub) are private personal tools and are not included. The fixtures hold only their OpenAPI schemas.

## License

MIT
