"""0.14.2: a foreign binary column in the facts table never reaches a tool result; a 400 to `dimensions` is named.

Both reported on a live store that moved to 0.14.1 from a hand-rolled embedding deployment (issue #3)."""
from __future__ import annotations

import json
import sqlite3
import urllib.error

import pytest

pytest.importorskip("numpy")

from hermes_plugin_holographic import HolographicMemoryProvider, embeddings  # noqa: E402

FACTS = ["The garden shed is painted green and has a red door",
         "The backup of the garden server runs every night at three",
         "The garden hose is kept behind the shed"]


def _provider(tmp_path, **config):
    p = HolographicMemoryProvider(config={"db_path": str(tmp_path / "memory_store.db"), "hrr_dim": 64, **config})
    p.initialize(session_id="s1")
    return p


def _legacy_column(tmp_path):
    """What the earlier deployment left behind: a BLOB column of its own vectors on every fact."""
    con = sqlite3.connect(tmp_path / "memory_store.db")
    con.execute("ALTER TABLE facts ADD COLUMN embedding BLOB")
    con.execute("UPDATE facts SET embedding = randomblob(64)")
    con.commit()
    con.close()


def test_a_foreign_blob_column_does_not_break_search(tmp_path):
    p = _provider(tmp_path)
    ids = [p._store.add_fact(f) for f in FACTS]
    _legacy_column(tmp_path)
    for query in ("garden shed", "when does the garden backup run?", "hose"):
        raw = p.handle_tool_call("fact_store", {"action": "search", "query": query})
        found = json.loads(raw)  # 0.14.1: "Object of type bytes is not JSON serializable" on every search
        assert found["results"], query
        assert all("embedding" not in f and "hrr_vector" not in f for f in found["results"])
    rows = p._retriever._rows(ids)
    assert len(rows) == 3 and all("embedding" not in r for r in rows)
    assert f"#{ids[2]} " in p.prefetch("where is the garden hose kept?", session_id="x")
    p.shutdown()


def test_our_own_vector_still_scores(tmp_path):
    """The filter keeps hrr_vector: the holographic term still reads it (and it is popped before output)."""
    p = _provider(tmp_path)
    for f in FACTS:
        p._store.add_fact(f)
    _legacy_column(tmp_path)
    cands = p._retriever._fts_candidates("garden shed", None, 0.0, 10)
    assert cands and all("hrr_vector" in c for c in cands) and all("embedding" not in c for c in cands)
    p.shutdown()


class _Refuses400:
    def open(self, request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, None)


ON = {"embeddings": "on", "embedding_url": "https://embeddings.example.net/v1", "embedding_model": "bge-m3",
      "embedding_key_env": "TEST_EMBEDDINGS_KEY"}


@pytest.mark.parametrize("dimensions, hinted", [(1024, True), (None, False)])
def test_a_400_to_dimensions_names_the_setting(monkeypatch, dimensions, hinted):
    monkeypatch.setattr(embeddings, "_OPENER", _Refuses400())
    monkeypatch.setenv("TEST_EMBEDDINGS_KEY", "sk-test-not-a-real-key")
    config = {**ON, **({"embedding_dimensions": dimensions} if dimensions else {})}
    e = embeddings.Embedder(embeddings.Settings(config))
    assert e.embed(["a"]) is None
    status = e.status()
    assert status.startswith("paused after an error (HTTP 400")
    assert ("embedding_dimensions is set" in status) is hinted
    assert "sk-test" not in status
