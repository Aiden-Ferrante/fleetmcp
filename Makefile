PY ?= .venv/bin/python

.PHONY: install test lint check demo transcript

install:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements.txt ruff mypy types-PyYAML

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .
	$(PY) -m mypy

check: lint test
	$(PY) -m fleetmcp.gen --check

# Start the Mission Control stub, drive the MCP server over stdio against it.
demo:
	$(PY) -m demo.run_demo

# Re-record the committed transcript from a real run.
transcript:
	$(PY) -m demo.run_demo > demo/transcript.txt 2>&1
	@cat demo/transcript.txt
