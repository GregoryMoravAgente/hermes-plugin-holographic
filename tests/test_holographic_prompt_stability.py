"""0.14.0: the system-prompt block is byte-stable while the store changes (after adurham's fork, 220292a).

The block renders near the top of the system prompt. Prompt caches that reuse the longest identical prefix re-read
everything after the first differing token; the block used to carry a live fact count, which changed with every stored
fact and cost a ~21K-token re-read at each new session. A fact is stored between two renders and the block must not
change."""
from __future__ import annotations

import re

from hermes_plugin_holographic import HolographicMemoryProvider


def _provider(tmp_path, **config):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "memory_store.db"), "hrr_dim": 64, **config})
    p.initialize(session_id="s1")
    return p


def test_the_block_does_not_change_when_a_fact_is_stored(tmp_path):
    provider = _provider(tmp_path)
    # empty -> not empty flips once per store; every later write must leave the block as it was
    provider._store.add_fact("The first fact makes the store non-empty")
    before = provider.system_prompt_block()
    provider._store.add_fact("A second fact stored between two renders of the block")
    provider._store.add_fact("A third fact, for good measure")
    assert provider.system_prompt_block() == before
    provider.shutdown()


def test_the_status_line_carries_no_number(tmp_path):
    provider = _provider(tmp_path)
    provider._store.add_fact("A fact so the status line is the non-empty one")
    status = provider.system_prompt_block().splitlines()[1]
    assert status.startswith("Active") and not re.search(r"\d", status)
    provider.shutdown()


def test_an_empty_store_still_says_so(tmp_path):
    provider = _provider(tmp_path)
    assert "Active, empty: nothing stored yet." in provider.system_prompt_block()
    provider.shutdown()
