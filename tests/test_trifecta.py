"""Every curated tool carries a trifecta classification, and the totals the
README quotes are the totals the file holds."""
from __future__ import annotations

from fleetmcp import trifecta


def test_every_tool_is_classified():
    tools = trifecta.load()
    assert len(tools) == 50
    for t in tools:
        assert t.get("trifecta_why"), t["name"]


def test_totals_match_the_readme():
    c = trifecta.counts(trifecta.load())
    assert (c["tools"], c["private_data"], c["untrusted_content"], c["egress"]) == (50, 37, 37, 0)


def test_no_tool_holds_all_three_legs():
    assert trifecta.counts(trifecta.load())["all_three"] == 0
