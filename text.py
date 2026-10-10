"""Text normalisation shared by search, the prefetch gate, entity matching and duplicate checks.

Words are compared by their Snowball stem (English for Latin script, Russian for Cyrillic; other scripts as-is), so
"Маши" finds "Маша" and "deploys" finds "deploy". A stem is not a dictionary lemma: it is a search key, used as an
FTS5 prefix next to the exact word, never instead of it. Identifiers (anything with a digit, or a dotted / dashed
token) are never stemmed and never treated as stopwords."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

try:  # pure Python, BSD; optional: without it words compare exactly, as before 0.3.0
    import snowballstemmer
    _STEMMERS = {"english": snowballstemmer.stemmer("english"), "russian": snowballstemmer.stemmer("russian")}
except ImportError:  # pragma: no cover - exercised only without the dependency
    _STEMMERS = {}

PUNCT = ".,;:!?\"'()[]{}#@<>«»“”„…"
_RE_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_RE_LATIN = re.compile(r"[a-z]")
_MIN_STEM = 3  # shorter stems match too much as prefixes ("ма*")

STOPWORDS = frozenset("""
    a about above after again all am an and any are as at be because been before being between both but by can could
    did do does doing don down during each few for from further had has have having he her here hers herself him himself
    his how i if in into is it its itself just me more most my myself no nor not now of off on once only or other our
    ours ourselves out over own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves ok okay yes yeah thanks thank please hi hello hey
    а без более бы был была были было быть в вам вас весь во вот все всего всех вы где да даже для до его ее её если
    есть еще ещё же за здесь и из или им их к как ко когда кто ли либо мне может мы на над надо наш не него нее неё нет
    ни них но ну о об однако он она они оно от очень по под при про с со так также такой там те тебе тем то того тоже
    той только том ты у уже хотя чего чей чем что чтобы чье чья эта эти это этот я ага ок окей спасибо привет пожалуйста
    да нет ладно хорошо понял поняла давай конечно просто вообще кстати понятно ясно угу кого кому чему чем ведь вон""".split())


def is_identifier(token: str) -> bool:
    """'pve-01', 'v0.21.6', 'gpt-4o', 'host:22': kept exact, never stemmed or dropped."""
    return any(c.isdigit() for c in token) or (len(token) > 2 and any(c in token for c in ".-_:/"))


@lru_cache(maxsize=8192)
def stem(word: str) -> str:
    """Snowball stem of a lower-cased word by script; the word itself for other scripts or identifiers."""
    if not _STEMMERS or is_identifier(word):
        return word
    if _RE_CYRILLIC.search(word):
        return _STEMMERS["russian"].stemWord(word)
    if _RE_LATIN.search(word):
        return _STEMMERS["english"].stemWord(word)
    return word


def words(text: str) -> list[str]:
    """Lower-cased whitespace tokens with surrounding punctuation stripped."""
    return [t for t in (w.strip(PUNCT) for w in (text or "").lower().split()) if t]


def stems(text: str) -> set[str]:
    return {stem(w) for w in words(text)}


def content_stems(text: str) -> set[str]:
    """Stems of the informative words only (stopwords dropped)."""
    return {stem(w) for w in words(text) if is_identifier(w) or (w not in STOPWORDS and len(w) > 1)}


# FTS5 syntax characters (quotes, grouping, prefix, column filter, NOT/required). Never interpolated into a MATCH
# expression: inside a token they split it into a phrase of the tokens unicode61 indexes ("pve-01" -> "pve 01").
FTS_OPERATORS = str.maketrans({c: " " for c in '"()*^:-+'})
NEGATORS = frozenset("""not no never nobody nothing cannot don't doesn't didn't isn't aren't wasn't weren't won't can't
    couldn't shouldn't haven't hasn't dont doesnt didnt isnt cant wont не нет никогда ни""".split())


@dataclass(frozen=True)
class Term:
    """One informative query term: the exact word, its stem (search key), whether it is an identifier, and the
    FTS-safe tokens it is searched as (operator characters removed)."""
    word: str
    stem: str
    identifier: bool
    tokens: tuple[str, ...] = ()

    def fts(self, operators: dict | None = None) -> str:
        """FTS5 expression built only from operator-free tokens: the exact word (a phrase when it splits into
        several tokens), OR its stem as a prefix when the word is one plain token and the stem is long enough."""
        tokens = self.tokens or tuple(self.word.translate(FTS_OPERATORS).split())
        exact = '"' + " ".join(tokens) + '"'
        if self.identifier or len(tokens) != 1 or len(self.stem) < _MIN_STEM or self.stem != stem(tokens[0]):
            return exact
        if self.stem == tokens[0]:  # "байк" must still find "байке"
            return f'"{self.stem}"*'
        return f'({exact} OR "{self.stem}"*)'

    def found_in(self, fact_words: set[str], fact_stems: set[str], text: str) -> bool:
        """Does a fact contain this term: the same word, the same stem, or (identifiers and multi-token words) the
        same token run with word boundaries, so "pve-01" does not match inside "pve-010"."""
        if self.word in fact_words:
            return True
        if not self.identifier and len(self.tokens) <= 1 and self.stem in fact_stems:
            return True
        return re.search(r"(?<![\w-])" + re.escape(self.word) + r"(?![\w-])", text) is not None


def query_terms(text: str) -> list[Term]:
    """Distinct informative terms of a natural-language query, in order of appearance."""
    seen, terms = set(), []
    for w in words(text):
        tokens = tuple(w.translate(FTS_OPERATORS).split())
        if not tokens:
            continue
        ident = is_identifier(w)
        if not ident and (w in STOPWORDS or len(w) < 2):
            continue
        s = stem(tokens[0]) if len(tokens) == 1 else w
        if s not in seen:
            seen.add(s)
            terms.append(Term(w, s, ident, tokens))
    return terms
