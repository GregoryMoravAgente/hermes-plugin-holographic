"""hermes-memory-store — holographic memory plugin (MemoryProvider): structured fact storage with entity
resolution, trust scoring, and HRR-based compositional retrieval. Original plugin by dusterbloom (PR #2351).
Config in $HERMES_HOME/config.yaml under plugins.hermes-memory-store: db_path ($HERMES_HOME/memory_store.db),
auto_extract (false), default_trust (0.5), min_trust_threshold (0.3), temporal_decay_half_life (0),
hrr_dim (1024), hrr_weight (0.3), mirror_memory_writes (false)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error
from utils import is_truthy_value
from .store import DuplicateContentError, MemoryStore
from .retrieval import FactRetriever
from hermes_cli.config import cfg_get

logger = logging.getLogger(__name__)

# Expanded by initialize() against the profile that opens it. A concrete path is copied by a profile clone
# and outlives a rename, so it keeps naming the old profile's DB.
_DEFAULT_DB_PATH = "$HERMES_HOME/memory_store.db"


FACT_STORE_SCHEMA = {
    "name": "fact_store",
    "description": (
        "Deep structured memory with algebraic reasoning. Use alongside the memory tool — memory for always-on "
        "context, fact_store for deep recall and compositional queries.\n\nACTIONS (simple → powerful):\n"
        "• add — Store a fact the user would expect you to remember.\n• search — Keyword lookup ('editor config', 'deploy process').\n"
        "• probe — Entity recall: ALL facts about a person/thing.\n• related — What connects to an entity? Structural adjacency.\n"
        "• reason — Compositional: facts connected to MULTIPLE entities simultaneously.\n"
        "• contradict — Memory hygiene: find facts making conflicting claims.\n• update/remove/list — CRUD operations.\n\n"
        "Before answering questions about the user, search first; probe a named person or thing for everything "
        "linked to it, reason for facts linking several. Fact ids (#N) in recalled memory are what fact_feedback takes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "search", "probe", "related", "reason", "contradict", "update", "remove", "list"]},
            "content": {"type": "string", "description": "Fact content (required for 'add')."},
            "query": {"type": "string", "description": "Search query (required for 'search')."},
            "entity": {"type": "string", "description": "Entity name for 'probe'/'related'."},
            "entities": {"type": "array", "items": {"type": "string"}, "description": "Entity names for 'reason'."},
            "fact_id": {"type": "integer", "description": "Fact ID for 'update'/'remove'."},
            "category": {"type": "string", "enum": ["user_pref", "project", "tool", "general"]},
            "tags": {"type": "string", "description": "Comma-separated tags."},
            "trust_delta": {"type": "number", "description": "Trust adjustment for 'update'."},
            "min_trust": {"type": "number", "description": "Minimum trust filter (default: 0.3)."},
            "limit": {"type": "integer", "description": "Max results (default: 10)."},
        },
        "required": ["action"],
    },
}

FACT_FEEDBACK_SCHEMA = {
    "name": "fact_feedback",
    "description": ("Rate a fact after using it. Mark 'helpful' if accurate, 'unhelpful' if outdated. "
                    "This trains the memory — good facts rise, bad facts sink."),
    "parameters": {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["helpful", "unhelpful"]},
                       "fact_id": {"type": "integer", "description": "The fact ID to rate."}},
        "required": ["action", "fact_id"],
    },
}

# Auto-extraction (on_session_end): (patterns, category) — user preferences -> user_pref, decisions -> project.
_EXTRACT_CATEGORIES = (
    ([re.compile(r'\bI\s+(?:prefer|like|love|use|want|need)\s+(.+)', re.IGNORECASE),
      re.compile(r'\bmy\s+(?:favorite|preferred|default)\s+\w+\s+is\s+(.+)', re.IGNORECASE),
      re.compile(r'\bI\s+(?:always|never|usually)\s+(.+)', re.IGNORECASE)], "user_pref"),
    ([re.compile(r'\bwe\s+(?:decided|agreed|chose)\s+(?:to\s+)?(.+)', re.IGNORECASE),
      re.compile(r'\bthe\s+project\s+(?:uses|needs|requires)\s+(.+)', re.IGNORECASE)], "project"),
)


def _load_plugin_config() -> dict:
    try:
        from hermes_cli.config import load_config_readonly  # canonical: managed-scope overlay + ${VAR} expansion
        return cfg_get(load_config_readonly(), "plugins", "hermes-memory-store", default={}) or {}
    except Exception:
        return {}


def _results(items: list, key: str = "results") -> str:
    return json.dumps({key: items, "count": len(items)})


_MAX_LIMIT = 100
# Agent contexts that may write (contract: skip writes for subagent / cron / flush contexts).
_WRITER_CONTEXTS = frozenset({"", "primary"})


def _limit(args: dict) -> int:
    """'limit' clamped to 1..100: a negative LIMIT means 'no limit' to SQLite and slices from the end in Python."""
    return max(1, min(_MAX_LIMIT, int(args.get("limit", 10))))


_WRITE_ACTIONS = frozenset({"add", "update", "remove"})


def _tool_handler(actions: dict):
    """(self, args) handler dispatching on args["action"] over ``actions``; unknown action -> tool_error."""
    return lambda self, args: (actions[args["action"]](self, args) if args["action"] in actions
                               else tool_error(f"Unknown action: {args['action']}"))


class HolographicMemoryProvider(MemoryProvider):
    """Holographic memory with structured facts, entity resolution, and HRR retrieval."""

    def __init__(self, config: dict | None = None):
        self._config = config or _load_plugin_config()
        self._store = self._retriever = None
        self._min_trust = float(self._config.get("min_trust_threshold", 0.3))
        self._writable = True

    @property
    def name(self) -> str:
        return "holographic"

    def is_available(self) -> bool:
        return True  # SQLite is always available, numpy is optional

    def save_config(self, values, hermes_home):
        """Write config to *hermes_home*'s config.yaml under plugins.hermes-memory-store."""
        # The canonical writer: config lock, managed-mode refusal, default stripping, atomic replace.
        # ``merge_existing`` keeps every other section. The home override makes it write the profile being
        # configured, not whichever profile is active in this process.
        from hermes_cli.config import save_config
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        values = dict(values)
        # This profile's own DB spelled out (older setups wrote it; the dashboard form re-submits what it
        # read) pins every clone and rename of the profile to this file, so it is stored as the placeholder.
        db_path = values.get("db_path")
        if isinstance(db_path, str) and Path(db_path).expanduser() == Path(hermes_home) / "memory_store.db":
            values["db_path"] = _DEFAULT_DB_PATH
        token = set_hermes_home_override(hermes_home)
        try:
            save_config({"plugins": {"hermes-memory-store": values}}, merge_existing=True)
        finally:
            reset_hermes_home_override(token)

    def get_config_schema(self):
        """Setup fields. ``default`` is the value saved now (falling back to the factory default), so re-running
        ``hermes memory setup`` keeps the current database and switches instead of resetting them."""
        def current(key: str, factory: str) -> str:
            value = self._config.get(key, factory)
            return ("true" if value else "false") if isinstance(value, bool) else str(value)

        return [
            {"key": "db_path", "description": "SQLite database path", "default": current("db_path", _DEFAULT_DB_PATH)},
            {"key": "auto_extract", "description": "Auto-extract facts at session end",
             "default": current("auto_extract", "false"), "choices": ["true", "false"]},
            {"key": "default_trust", "description": "Default trust score for new facts", "default": current("default_trust", "0.5")},
            {"key": "hrr_dim", "description": "HRR vector dimensions", "default": current("hrr_dim", "1024")},
            {"key": "mirror_memory_writes", "description": "Also store every built-in memory 'add' (MEMORY.md / USER.md) "
             "as a fact. Off: those entries are already in the system prompt in full",
             "default": current("mirror_memory_writes", "false"), "choices": ["true", "false"]},
        ]

    def _db_path(self, hermes_home: str) -> str:
        db_path = self._config.get("db_path", _DEFAULT_DB_PATH)
        if isinstance(db_path, str):  # expand $HERMES_HOME so paths resolve to the profile that opens it
            db_path = db_path.replace("$HERMES_HOME", hermes_home).replace("${HERMES_HOME}", hermes_home)
        return db_path

    def backup_paths(self) -> list[str]:
        """The database when it lives outside HERMES_HOME (``hermes backup`` already covers the home)."""
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
        db_path = self._db_path(str(home))
        if not isinstance(db_path, str) or db_path == ":memory:":
            return []
        path = Path(db_path).expanduser()
        try:
            path.resolve().relative_to(home.resolve())
            return []
        except (ValueError, OSError):
            return [str(path.resolve())] if path.is_absolute() or path.exists() else []

    def initialize(self, session_id: str, **kwargs) -> None:
        from hermes_constants import get_hermes_home
        _hermes_home = str(kwargs.get("hermes_home") or get_hermes_home())
        if self._store is not None:  # re-initialised: release the previous handle first
            self.shutdown()
        # Contract: subagent / cron / flush contexts read but never write (facts, feedback, counters, extraction).
        self._writable = str(kwargs.get("agent_context") or "primary") in _WRITER_CONTEXTS
        hrr_dim = int(self._config.get("hrr_dim", 1024))
        self._store = MemoryStore(db_path=self._db_path(_hermes_home), default_trust=float(self._config.get("default_trust", 0.5)),
                                  hrr_dim=hrr_dim)
        self._retriever = FactRetriever(store=self._store, hrr_dim=hrr_dim, hrr_weight=float(self._config.get("hrr_weight", 0.3)),
                                        temporal_decay_half_life=int(self._config.get("temporal_decay_half_life", 0)))
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        try:
            total = self._store.count_facts()
        except Exception:
            total = 0
        body = ("Active. Empty fact store — proactively add facts the user would expect you to remember.\n"
                "Use fact_store(action='add') to store durable structured facts about people, projects, preferences, decisions.\n"
                if total == 0 else
                f"Active. {total} facts stored with entity resolution and trust scoring.\n"
                "Use fact_store to search, probe entities, reason across entities, or add facts.\n")
        return "# Holographic Memory\n" + body + "Use fact_feedback to rate facts after using them (trains trust scores)."

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._retriever or not query:
            return ""
        try:
            results = self._counted(self._retriever.search(query, min_trust=self._min_trust, limit=5))
            lines = [f"- #{r.get('fact_id')} [{r.get('trust_score', r.get('trust', 0)):.1f}] {r.get('content', '')}"
                     for r in results]
            return "## Holographic Memory\n" + "\n".join(lines) if results else ""
        except Exception as e:
            logger.debug("Holographic prefetch failed: %s", e)
            return ""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [FACT_STORE_SCHEMA, FACT_FEEDBACK_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name not in self._TOOL_HANDLERS:
            return tool_error(f"Unknown tool: {tool_name}")
        if not self._writable and (tool_name == "fact_feedback" or args.get("action") in _WRITE_ACTIONS):
            return tool_error("The fact store is read-only in this context (subagent, cron or flush); "
                              "only the main agent writes facts.")
        try:
            return self._TOOL_HANDLERS[tool_name](self, args)
        except KeyError as exc:
            return tool_error(f"Missing required argument: {exc}")
        except DuplicateContentError as exc:
            return json.dumps({"updated": False, "duplicate_of": exc.fact_id,
                               "reason": "another fact already says exactly this; remove one of them instead"})
        except Exception as exc:
            return tool_error(str(exc))

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        # is_truthy_value: auto_extract is a string enum ("false"/"true"); plain truthiness would treat "false" as on.
        if self._writable and is_truthy_value(self._config.get("auto_extract", False)) and self._store and messages:
            self._auto_extract_facts(messages)

    def on_memory_write(self, action: str, target: str, content: str, metadata: dict | None = None) -> None:
        """Mirror built-in memory writes as facts, only with ``mirror_memory_writes`` on.

        Off by default: MEMORY.md / USER.md already sit in the system prompt in full, so the copy added no
        knowledge, prefetch injected the same sentence a second time, and the copy outlived the entry when it was
        reworded or removed. When on, ``replace`` and ``remove`` follow the entry through
        ``metadata["previous_content"]`` (the exact old entry; ``old_text`` is only a search fragment)."""
        if not (self._writable and self._store and is_truthy_value(self._config.get("mirror_memory_writes", False))):
            return
        category = "user_pref" if target == "user" else "general"
        previous = (metadata or {}).get("previous_content") or ""
        try:
            old = self._store.read("SELECT fact_id FROM facts WHERE content = ?", (previous.strip(),)) if previous.strip() else []
            if action == "add" and content:
                self._store.add_fact(content, category=category)
            elif action == "replace" and content:
                if old:
                    try:
                        self._store.update_fact(old[0]["fact_id"], content=content)
                    except DuplicateContentError:
                        self._store.remove_fact(old[0]["fact_id"])  # the new text is already a fact
                else:
                    self._store.add_fact(content, category=category)
            elif action == "remove" and old:
                self._store.remove_fact(old[0]["fact_id"])
        except Exception as e:
            logger.debug("Holographic memory_write mirror failed: %s", e)

    def shutdown(self) -> None:
        # Close on the caller's thread: leaving the shared connection (+ write lock) to GC keeps it alive on a gateway.
        if self._store is not None:
            try:
                self._store.close()
            except Exception as e:
                logger.debug("Holographic shutdown close() failed: %s", e)
        self._store = self._retriever = None

    # Tool handlers (self, args) -> str. KeyError from args[...] / Exception -> tool_error in handle_tool_call;
    # argument coercion order (and therefore which error surfaces first) mirrors the underlying call order.

    def _counted(self, results: list) -> list:
        """Count the facts handed to the model as recall (prefetch, search/probe/related/reason; never list or
        contradict). ``retrieval_count`` was declared and never written, so "nobody ever used this fact" was true
        of every fact. A failed count never breaks retrieval; read-only contexts do not count."""
        if not self._writable:
            return results
        try:
            self._store.record_retrievals(r.get("fact_id") for r in results)
        except Exception as e:
            logger.debug("Holographic retrieval count failed: %s", e)
        return results

    def _entity_query(self, method: str, a: dict) -> str:
        """'probe' / 'related': single-entity retriever queries."""
        return _results(self._counted(getattr(self._retriever, method)(
            a["entity"], category=a.get("category"), limit=_limit(a), min_trust=self._trust_floor(a))))

    def _trust_floor(self, a: dict) -> float:
        return float(a.get("min_trust", self._min_trust))

    def _add(self, a: dict) -> str:
        fact_id, created = self._store.add_fact_status(a["content"], category=a.get("category", "general"), tags=a.get("tags", ""))
        return json.dumps({"fact_id": fact_id, "status": "added" if created else "exists"})

    def _feedback(self, a: dict) -> str:
        if a["action"] not in ("helpful", "unhelpful"):
            return tool_error("action must be 'helpful' or 'unhelpful'")
        return json.dumps(self._store.record_feedback(int(a["fact_id"]), helpful=a["action"] == "helpful"))

    _TOOL_HANDLERS = {
        "fact_store": _tool_handler({
            "add": lambda self, a: self._add(a),
            "search": lambda self, a: _results(self._counted(self._retriever.search(
                a["query"], category=a.get("category"), min_trust=self._trust_floor(a), limit=_limit(a)))),
            "probe": lambda self, a: self._entity_query("probe", a),
            "related": lambda self, a: self._entity_query("related", a),
            "reason": lambda self, a: _results(self._counted(
                self._retriever.reason(a["entities"], category=a.get("category"), limit=_limit(a), min_trust=self._trust_floor(a))))
            if a.get("entities") else tool_error("reason requires 'entities' list"),
            "contradict": lambda self, a: _results(self._retriever.contradict(
                category=a.get("category"), limit=_limit(a), min_trust=float(a.get("min_trust", 0.0)))),
            "update": lambda self, a: json.dumps({"updated": self._store.update_fact(
                int(a["fact_id"]), content=a.get("content"), trust_delta=float(a["trust_delta"]) if "trust_delta" in a else None,
                tags=a.get("tags"), category=a.get("category"))}),
            "remove": lambda self, a: json.dumps({"removed": self._store.remove_fact(int(a["fact_id"]))}),
            "list": lambda self, a: _results(self._store.list_facts(
                category=a.get("category"), min_trust=float(a.get("min_trust", 0.0)), limit=_limit(a)), key="facts"),
        }),
        "fact_feedback": lambda self, a: self._feedback(a),
    }

    def _auto_extract_facts(self, messages: list) -> None:
        # Compaction handoff summaries arrive as role="user" and match the decision patterns; never store the
        # compactor's own output as a fact. A merge-into-tail row holds genuine prior user text BEFORE
        # _MERGED_SUMMARY_DELIMITER (after the header) and the summary AFTER it — harvest only that segment.
        from agent.context_compressor import _MERGED_PRIOR_CONTEXT_HEADER, _MERGED_SUMMARY_DELIMITER, is_compaction_summary_message  # heavy; lazy
        extracted = 0
        for msg in messages:
            content = msg.get("content", "") if msg.get("role") == "user" else None
            pre = content.split(_MERGED_SUMMARY_DELIMITER, 1)[0].removeprefix(_MERGED_PRIOR_CONTEXT_HEADER).strip() \
                if isinstance(content, str) and _MERGED_SUMMARY_DELIMITER in content else ""
            if pre:
                content = pre
            elif content is None or is_compaction_summary_message(msg):
                continue
            if not isinstance(content, str) or len(content) < 10:
                continue
            for patterns, category in _EXTRACT_CATEGORIES:
                if any(p.search(content) for p in patterns):
                    try:
                        self._store.add_fact(content[:400], category=category)
                        extracted += 1
                    except Exception:
                        pass
        if extracted:
            logger.info("Auto-extracted %d facts from conversation", extracted)

def register(ctx) -> None:
    """Register the holographic memory provider with the plugin system."""
    ctx.register_memory_provider(HolographicMemoryProvider(config=_load_plugin_config()))
