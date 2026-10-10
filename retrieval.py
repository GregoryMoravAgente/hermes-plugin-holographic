"""Fact retrieval for the memory store: FTS5 keyword candidates reranked with Jaccard and HRR similarity, weighted by
trust (ported from KIK memory_agent.py); entity recall straight from the entity index."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import MemoryStore

from . import holographic as hrr

logger = logging.getLogger(__name__)

_FACT_COLUMNS = "fact_id, content, category, tags, trust_score, retrieval_count, helpful_count, created_at, updated_at"
_F_COLUMNS = ", ".join(f"f.{c.strip()}" for c in _FACT_COLUMNS.split(","))
_ROLE_ENTITY, _ROLE_CONTENT = hrr.ROLE_ENTITY, hrr.ROLE_CONTENT
_PUNCT = ".,;:!?\"'()[]{}#@<>"
# FTS5 operator characters. Inside a token they become spaces of a quoted phrase: "pve-01" -> "pve 01", the same two
# tokens unicode61 indexed, adjacent (deleting them gave "pve01", which matches nothing).
_FTS_OPERATORS = str.maketrans({c: " " for c in '"()*^:-+'})
# Stopwords dropped before FTS5 OR-expansion: short English function words that
# carry no retrieval signal and force false-negative AND matches.
_FTS_STOPWORDS = frozenset("""
    a about above after again all am an and any are as at be because been before being between both but by can could
    did do does doing don down during each few for from further had has have having he her here hers herself him himself
    his how i if in into is it its itself just me more most my myself no nor not now of off on once only or other our
    ours ourselves out over own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves""".split())


def _shift(sim: float) -> float:
    """Cosine similarity [-1, 1] -> [0, 1]."""
    return (sim + 1.0) / 2.0


def _related_floor(dim: int) -> float:
    """Residual similarity a fact must reach to count as structurally tied to an entity in `related`. Measured at
    dim=1024 on 400 facts with one to four entities: tied facts >= 0.35, the best of 17k untied pairs 0.088; 0.2 sits
    in the gap with a wide margin. Small dimensions get a proportionally higher floor (8 sd of chance similarity)."""
    return min(0.5, max(0.2, 8.0 * math.sqrt(0.5 / max(dim, 1))))


class FactRetriever:
    """Multi-strategy fact retrieval with trust-weighted scoring."""

    def __init__(self, store: MemoryStore, temporal_decay_half_life: int = 0,  # days, 0 = disabled
                 fts_weight: float = 0.4, jaccard_weight: float = 0.3, hrr_weight: float = 0.3, hrr_dim: int = 1024):
        self.store, self.half_life, self.hrr_dim = store, temporal_decay_half_life, hrr_dim
        if hrr_weight > 0 and not hrr._HAS_NUMPY:  # redistribute weights without numpy
            fts_weight, jaccard_weight, hrr_weight = 0.6, 0.4, 0.0
        self.fts_weight, self.jaccard_weight, self.hrr_weight = fts_weight, jaccard_weight, hrr_weight

    def _atom(self, word: str):
        return hrr.encode_atom(word, self.hrr_dim)

    def _phases(self, blob: bytes):
        """Decode a stored vector; None for a blob of another dimension or a damaged one (logged, row skipped)."""
        try:
            return hrr.bytes_to_phases(blob, dim=self.hrr_dim)
        except (ValueError, TypeError) as exc:
            logger.warning("holographic: skipping an unreadable HRR vector (%s)", exc)
            return None

    def search(self, query: str, category: str | None = None, min_trust: float = 0.3, limit: int = 10) -> list[dict]:
        """FTS5 candidates (limit*3) → Jaccard + HRR rerank → trust weighting → optional temporal decay
        0.5^(age_days / half_life). Returns fact dicts with 'score', sorted desc."""
        return self._rerank(query, self._fts_candidates(query, category, min_trust, limit * 3), limit)

    def _rerank(self, query: str, candidates: list[dict], limit: int) -> list[dict]:
        query_tokens = self._tokenize(query)
        # Query vector is loop-invariant; encode lazily on the first candidate that carries an HRR vector
        # so stores whose hrr_vector was never backfilled don't pay for it.
        query_vec = None  # bind(encode_text(query), ROLE_CONTENT): the shape a fact's content is stored in
        for fact in candidates:
            jaccard = self._jaccard_similarity(query_tokens, self._tokenize(fact["content"]) | self._tokenize(fact.get("tags", "")))
            hrr_sim = 0.5  # neutral
            fact_vec = self._phases(fact["hrr_vector"]) if self.hrr_weight > 0 and fact.get("hrr_vector") else None
            if fact_vec is not None:
                if query_vec is None:
                    query_vec = hrr.bind(hrr.encode_text(query, self.hrr_dim), self._atom(_ROLE_CONTENT))
                hrr_sim = _shift(hrr.similarity(query_vec, fact_vec))
            relevance = self.fts_weight * fact.get("fts_rank", 0.0) + self.jaccard_weight * jaccard + self.hrr_weight * hrr_sim
            fact["score"] = relevance * fact["trust_score"]
            if self.half_life > 0:
                fact["score"] *= self._temporal_decay(fact.get("updated_at") or fact.get("created_at"))
        results = sorted(candidates, key=lambda x: x["score"], reverse=True)[:limit]
        for fact in results:
            fact.pop("hrr_vector", None)  # callers expect JSON-serializable dicts
        return results

    def _linked_facts(self, entity_groups: list[list[int]], category: str | None, min_trust: float, limit: int) -> list[dict]:
        """Facts linked to at least one entity of EVERY group (each group = the ids one name resolves to)."""
        joins = " INTERSECT ".join(
            f"SELECT fact_id FROM fact_entities WHERE entity_id IN ({','.join('?' * len(g))})" for g in entity_groups)
        params = [i for g in entity_groups for i in g] + [min_trust] + ([category] if category else []) + [limit]
        sql = (f"SELECT {_F_COLUMNS} FROM facts f WHERE f.fact_id IN ({joins}) AND f.trust_score >= ? "
               + ("AND f.category = ? " if category else "") + "ORDER BY f.trust_score DESC, f.updated_at DESC LIMIT ?")
        results = [dict(r) for r in self.store.read(sql, params)]
        for fact in results:
            fact["score"] = fact["trust_score"]
        return results

    def probe(self, entity: str, category: str | None = None, limit: int = 10, min_trust: float = 0.3) -> list[dict]:
        """Every fact linked to the entity (exact, by the entity index; name or alias, any letter case), most trusted
        first. A name never linked as an entity (e.g. a one-word name) falls back to keyword search."""
        ids = self.store.entity_ids(entity)
        if not ids:
            return self.search(entity, category=category, min_trust=min_trust, limit=limit)
        return self._linked_facts([ids], category, min_trust, limit)

    def reason(self, entities: list[str], category: str | None = None, limit: int = 10, min_trust: float = 0.3) -> list[dict]:
        """Facts linked to ALL the given entities. When some name is not a known entity, keyword search that still
        requires every name (FTS5 AND of the names as phrases), never facts about just one of them."""
        names = [e for e in (entities or []) if isinstance(e, str) and e.strip()]
        if not names:
            return []
        groups = [self.store.entity_ids(name) for name in names]
        if all(groups):
            return self._linked_facts(groups, category, min_trust, limit)
        phrases = [self._fts_phrase(name) for name in names]
        if not all(phrases):
            return []
        return self._rerank(" ".join(names), self._fts_query(" AND ".join(phrases), category, min_trust, limit * 3), limit)

    def related(self, entity: str, category: str | None = None, limit: int = 10, min_trust: float = 0.3) -> list[dict]:
        """Facts structurally connected to an entity (the entity's atom unbinds to a role), above a noise floor.
        Keyword search without numpy, vectors, or any fact over the floor."""
        if not hrr._HAS_NUMPY:
            return self.search(entity, category=category, min_trust=min_trust, limit=limit)
        entity_vec = self._atom(entity.strip().lower())  # bare atom, not role-bound: ANY structural match
        roles = (self._atom(_ROLE_ENTITY), self._atom(_ROLE_CONTENT))  # loop-invariant: encode once
        floor = _related_floor(self.hrr_dim)
        scored = []
        for row in self._vector_rows(category, min_trust):
            fact = dict(row)
            fact_vec = self._phases(fact.pop("hrr_vector"))
            if fact_vec is None:
                continue
            sim = max(hrr.similarity(hrr.unbind(fact_vec, entity_vec), role) for role in roles)
            if sim >= floor:
                fact["score"] = _shift(sim) * fact["trust_score"]
                scored.append(fact)
        if not scored:
            return self.search(entity, category=category, min_trust=min_trust, limit=limit)
        return sorted(scored, key=lambda x: x["score"], reverse=True)[:limit]

    def contradict(self, category: str | None = None, threshold: float = 0.3, limit: int = 10,
                   min_trust: float = 0.0) -> list[dict]:
        """Candidate conflicts: pairs about the same subject (shared linked entities) making a similar but not
        identical claim ("Alice lives in Paris" / "Alice lives in London", a changed number, a flipped preference).
        Score = entity overlap x word overlap of the two claims with the entity names left out. Identical claims,
        a claim that only adds detail to the other ("likes apples" / "likes apples and bananas") and different
        topics about the same subject do not qualify. ``content_similarity`` is that word overlap. Word sets, so
        word order is ignored. Works without numpy."""
        sql = ("SELECT fact_id, content, category, tags, trust_score, created_at, updated_at FROM facts "
               "WHERE trust_score >= ?" + (" AND category = ?" if category else ""))
        rows = self.store.read(sql, [min_trust] + ([category] if category else []))
        if len(rows) < 2:
            return []
        if len(rows) > 500:  # O(n^2) guard: only compare the most recently updated facts
            rows = sorted(rows, key=lambda r: r["updated_at"] or r["created_at"], reverse=True)[:500]
        names_by_fact: dict[int, set[str]] = {}
        for r in self.store.read("SELECT fe.fact_id, e.name FROM fact_entities fe JOIN entities e ON e.entity_id = fe.entity_id"):
            names_by_fact.setdefault(r["fact_id"], set()).add(r["name"].lower())
        facts = []  # (public dict, lower-cased entity names, claim words without the entity words)
        for row in rows:
            names = names_by_fact.get(row["fact_id"])
            if names:
                entity_words = {w for name in names for w in self._tokenize(name)}
                facts.append((dict(row), names, self._tokenize(row["content"]) - entity_words))
        contradictions = []
        for i, (f1, ents1, claim1) in enumerate(facts):
            for f2, ents2, claim2 in facts[i + 1:]:
                entity_overlap = len(ents1 & ents2) / len(ents1 | ents2)
                if entity_overlap < 0.3 or claim1 <= claim2 or claim2 <= claim1:
                    continue  # different subjects, the same claim, or one claim refining the other
                claim_sim = self._jaccard_similarity(claim1, claim2)
                contradiction_score = entity_overlap * claim_sim
                if contradiction_score >= threshold:
                    contradictions.append({
                        "fact_a": f1, "fact_b": f2,
                        "entity_overlap": round(entity_overlap, 3),
                        "content_similarity": round(claim_sim, 3),
                        "contradiction_score": round(contradiction_score, 3),
                        "shared_entities": sorted(ents1 & ents2),
                    })
        return sorted(contradictions, key=lambda x: x["contradiction_score"], reverse=True)[:limit]

    def _vector_rows(self, category: str | None, min_trust: float = 0.0) -> list:
        """All facts that carry an HRR vector and pass the trust filter, optionally within a category."""
        where = "WHERE hrr_vector IS NOT NULL AND trust_score >= ?" + (" AND category = ?" if category else "")
        return self.store.read(f"SELECT {_FACT_COLUMNS}, hrr_vector FROM facts {where}",
                               [min_trust] + ([category] if category else []))

    def _fts_candidates(self, query: str, category: str | None, min_trust: float, limit: int) -> list[dict]:
        """FTS5 candidates for a natural-language query; [] when nothing in it is searchable."""
        expression = self._sanitize_fts_query(query)
        return self._fts_query(expression, category, min_trust, limit) if expression else []

    def _fts_query(self, expression: str, category: str | None, min_trust: float, limit: int) -> list[dict]:
        """Raw FTS5 MATCH candidates with rank normalized to [0, 1] as 'fts_rank'."""
        category_clause = "AND f.category = ? " if category else ""
        params = [expression] + ([category] if category else []) + [min_trust, limit]
        sql = ("SELECT f.*, facts_fts.rank as fts_rank_raw FROM facts_fts JOIN facts f ON f.fact_id = facts_fts.rowid "
               f"WHERE facts_fts MATCH ? {category_clause}AND f.trust_score >= ? ORDER BY facts_fts.rank LIMIT ?")
        try:
            results = [dict(row) for row in self.store.read(sql, params)]
        except Exception as exc:  # a damaged index (expressions are built to be valid)
            # Logged, not raised: recall must not take the turn down; a silent [] hides a broken index.
            logger.warning("holographic: FTS5 search failed (%s: %s)", type(exc).__name__, exc)
            return []
        # FTS5 rank is negative (lower = better); normalize |rank| / max to [0, 1] (1e-6 floor avoids div by zero)
        max_rank = max([abs(f["fts_rank_raw"]) for f in results] + [1e-6])
        for fact in results:
            fact["fts_rank"] = abs(fact.pop("fts_rank_raw")) / max_rank
        return results

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Lowercase whitespace tokens with surrounding punctuation stripped (no stemming)."""
        return {c for c in (w.strip(_PUNCT) for w in text.lower().split()) if c} if text else set()

    @staticmethod
    def _fts_phrase(text: str) -> str:
        """Text as one FTS5 phrase (operator characters become spaces); "" when nothing is left."""
        words = [w.strip(_PUNCT) for w in text.lower().translate(_FTS_OPERATORS).split()]
        words = [w for w in words if w]
        return f'"{" ".join(words)}"' if words else ""

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Natural-language query -> FTS5-safe OR expression of quoted tokens. FTS5 AND-joins a multi-word
        MATCH by default, which tanks recall on prose: drop stopwords and <2-char tokens, turn FTS5 operator
        chars inside a token into a phrase ("pve-01" -> "pve 01"). Nothing left -> "" (no search, never a raw MATCH)."""
        if not query:
            return ""
        tokens = []
        for raw in query.lower().split():
            parts = [p.strip(_PUNCT) for p in raw.strip(_PUNCT).translate(_FTS_OPERATORS).split()]
            parts = [p for p in parts if p]
            if not parts or (len(parts) == 1 and (len(parts[0]) < 2 or parts[0] in _FTS_STOPWORDS)):
                continue
            tokens.append(f'"{" ".join(parts)}"')
        return " OR ".join(dict.fromkeys(tokens))

    @staticmethod
    def _jaccard_similarity(set_a: set, set_b: set) -> float:
        """Jaccard similarity coefficient: |A ∩ B| / |A ∪ B|."""
        return len(set_a & set_b) / len(set_a | set_b) if set_a and set_b else 0.0

    def _temporal_decay(self, timestamp_str: str | None) -> float:
        """0.5^(age_days / half_life); 1.0 if disabled, missing, unparseable, or in the future."""
        if not self.half_life or not timestamp_str:
            return 1.0
        try:
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")) if isinstance(timestamp_str, str) else timestamp_str
            age_days = (datetime.now(timezone.utc) - (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))).total_seconds() / 86400
            return 1.0 if age_days < 0 else math.pow(0.5, age_days / self.half_life)
        except (ValueError, TypeError):
            return 1.0
