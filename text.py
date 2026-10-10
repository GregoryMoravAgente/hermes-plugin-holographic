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
_LOOSE_FROM, _LOOSE_MIN = 7, 5  # stems this long also match on their first len-2 (at least 5) letters

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
    да нет ладно хорошо понял поняла давай конечно просто вообще кстати понятно ясно угу кого кому чему чем ведь вон
    чём всё моё твоё своё нём""".split())

# Whole-message greetings and courtesies: they name a time of day ("good morning") but ask nothing, so they must not
# recall facts that mention mornings. Compared after lower-casing and stripping punctuation and spaces.
GREETINGS = frozenset("""
    goodmorning goodafternoon goodevening goodnight morning gm gn hello hi hey hiall hellothere hithere howareyou
    thankyou thanksalot thankssomuch
    доброеутро добрыйдень добрыйвечер спокойнойночи добройночи утро привет приветик всемпривет здравствуй здравствуйте
    какдела спасибо спасибобольшое благодарю
    早上好 早安 上午好 中午好 下午好 晚上好 晚安 你好 您好 大家好 嗨 谢谢 多谢 非常感谢""".split())


def is_greeting(text: str) -> bool:
    key = "".join(ch for ch in (text or "").lower() if ch.isalnum())
    return key in GREETINGS


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
    """Stems of the informative words only (stopwords dropped); runs of unsegmented scripts contribute their
    content n-grams, so Chinese or Thai text compares by fragments rather than as one huge "word"."""
    out = set()
    for w in words(text):
        if UNSEGMENTED.search(w):
            out |= {g for g, weight in run_grams(w) if weight}
        elif is_identifier(w) or (w not in STOPWORDS and len(w) > 1):
            out.add(stem(w))
    return out


# Scripts written without spaces between words (Thai, Lao, Khmer, Myanmar, Chinese, Japanese) and Korean, whose
# particles attach to the word: the FTS5 unicode61 tokenizer indexes a whole run as one token, so these are matched
# by character n-grams against the text (bigrams for CJK and Hangul, trigrams for Southeast Asian scripts).
UNSEGMENTED = re.compile(r"[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff]+|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]+")
_SEA = re.compile(r"[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff]")
# Chinese function characters (particles, pronouns, question words, auxiliaries). A gram made only of them (可以, 什么,
# 怎么, 我们) carries no topic; a gram with one of them carries half; others carry full weight.
CJK_FUNCTION = frozenset("的是了吗呢吧啊呀哦嗯么怎什哪谁几多少在有和与及或就都也还要会能可以把被给让对为从到这那你我他她它们个一不没很太该应些吗嘛")
CJK_NEGATORS = frozenset("不没无别非未")
_MAX_GRAMS = 32
_LATIN_IN_CJK = re.compile(r"[A-Za-z][A-Za-z0-9._:/-]*[A-Za-z0-9]|[A-Za-z0-9]*\d[A-Za-z0-9._:/-]*")


def run_grams(run: str) -> list[tuple[str, float]]:
    """(n-gram, weight) of one unsegmented run; a run no longer than n is one gram."""
    n = 3 if _SEA.match(run) else 2
    grams = [run] if len(run) <= n else [run[i:i + n] for i in range(len(run) - n + 1)]
    out = []
    for g in grams:
        function = sum(c in CJK_FUNCTION for c in g)
        out.append((g, 0.0 if function == len(g) else 0.5 if function else 1.0))
    return out


def query_grams(text: str) -> list[tuple[str, float]]:
    """Weighted substrings to look for when *text* contains unsegmented script: its content n-grams, plus the Latin
    words and identifiers written into it ("HR24", "hr-ovh", "Python"), which the tokenizer glues to the
    neighbouring characters. Lower-cased; weight-0 grams dropped; [] for text without unsegmented script."""
    lowered = (text or "").lower()
    if not UNSEGMENTED.search(lowered):
        return []
    grams: dict[str, float] = {}
    for run in UNSEGMENTED.findall(lowered):
        for g, weight in run_grams(run):
            if weight:
                grams[g] = max(grams.get(g, 0.0), weight)
    for m in _LATIN_IN_CJK.finditer(UNSEGMENTED.sub(" ", lowered)):
        token = m.group(0).strip("._:/-")
        if len(token) >= 3 and token not in STOPWORDS:
            grams[token] = 1.0
    return list(grams.items())[:_MAX_GRAMS]


def content_chars(text: str) -> list[str]:
    """Distinct characters of the unsegmented runs that carry meaning (no function characters or negators)."""
    chars = []
    for run in UNSEGMENTED.findall((text or "").lower()):
        chars += [c for c in run if c not in CJK_FUNCTION and c not in CJK_NEGATORS and c.isalnum()]
    return list(dict.fromkeys(chars))


def gram_coverage(grams: list[tuple[str, float]], text: str) -> float:
    """Weighted share of the grams present in *text* (case-insensitive)."""
    total = sum(w for _, w in grams)
    if not total:
        return 0.0
    lowered = (text or "").lower()
    return sum(w for g, w in grams if g in lowered) / total


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

    @property
    def loose(self) -> str:
        """A shorter shared beginning for long words, so derivations Snowball keeps apart still meet
        ("переезжаем" / "переезд" share "переез"); "" for short words and identifiers."""
        if self.identifier or len(self.tokens) != 1 or len(self.stem) < _LOOSE_FROM:
            return ""
        return self.stem[:max(_LOOSE_MIN, len(self.stem) - 2)]

    def fts(self, operators: dict | None = None) -> str:
        """FTS5 expression built only from operator-free tokens: the exact word (a phrase when it splits into
        several tokens), OR its stem as a prefix when the word is one plain token and the stem is long enough."""
        tokens = self.tokens or tuple(self.word.translate(FTS_OPERATORS).split())
        exact = '"' + " ".join(tokens) + '"'
        if self.identifier or len(tokens) != 1 or len(self.stem) < _MIN_STEM or self.stem != stem(tokens[0]):
            return exact
        loose = f' OR "{self.loose}"*' if self.loose and self.loose != self.stem else ""
        if self.stem == tokens[0] and not loose:  # "байк" must still find "байке"
            return f'"{self.stem}"*'
        return f'({exact} OR "{self.stem}"*{loose})'

    def found_in(self, fact_words: set[str], fact_stems: set[str], text: str) -> bool:
        """Does a fact contain this term: the same word, the same stem, or (identifiers and multi-token words) the
        same token run with word boundaries, so "pve-01" does not match inside "pve-010"."""
        if self.word in fact_words:
            return True
        if not self.identifier and len(self.tokens) <= 1 and self.stem in fact_stems:
            return True
        if self.loose and any(w.startswith(self.loose) for w in fact_words):
            return True
        # An ASCII identifier is bounded by ASCII word characters only: in Chinese text "hr-ovh" is glued to the
        # characters around it ("服务器hr-ovh运行").
        edge = r"A-Za-z0-9_\-" if self.word.isascii() else r"\w\-"
        return re.search(rf"(?<![{edge}])" + re.escape(self.word) + rf"(?![{edge}])", text) is not None


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
