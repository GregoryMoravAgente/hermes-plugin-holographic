"""0.14.0: without numpy, search by meaning stays off and says why — checked in a fresh process where numpy cannot be
imported (the module with the other 0.14.0 tests needs numpy and is skipped without it)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent('''
    import importlib.util, json, os, sys
    sys.modules["numpy"] = None  # any "import numpy" raises ImportError from here on
    sys.path.insert(0, os.environ["HERMES_AGENT_REPO"])
    spec = importlib.util.spec_from_file_location("hermes_plugin_holographic", os.path.join(sys.argv[1], "__init__.py"),
                                                  submodule_search_locations=[sys.argv[1]])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    from hermes_plugin_holographic import embeddings
    p = mod.HolographicMemoryProvider(config={"db_path": sys.argv[2], "embeddings": "on",
                                              "embedding_url": "http://127.0.0.1:9/v1"})
    p.initialize(session_id="s")
    fid = p._store.add_fact("The nightly backup runs from the OES box at 2 am.")
    p.sync_turn("hello", "hi")
    block = p.prefetch("when does the backup run?")
    stats = json.loads(p.handle_tool_call("fact_store", {"action": "stats"}))
    print(json.dumps({"numpy": embeddings.np is not None, "status": stats["search_by_meaning"]["status"],
                      "recalled": f"#{fid} " in block, "calls": p._embedder.calls}))
    p.shutdown()
''')


def test_without_numpy_in_a_fresh_process(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    agent = os.environ.get("HERMES_AGENT_REPO") or str(Path.home() / ".hermes" / "hermes-agent")
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_AGENT_REPO=agent)
    out = subprocess.run([sys.executable, "-c", SCRIPT, str(REPO), str(tmp_path / "memory_store.db")], env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-3000:]
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result == {"numpy": False, "status": "unavailable: numpy is not installed", "recalled": True, "calls": 0}
