"""Build de_compounds.tsv.gz (German compound splitting) and de_es_forms.tsv.gz (German and Spanish verb forms that
Snowball keeps apart from their lemma) from Wikidata Lexemes.

    uv run --with snowballstemmer python -I tools/build_de_es_lexicon.py fetch <dir>   # the queries below -> CSV
    uv run --with snowballstemmer python -I tools/build_de_es_lexicon.py build <dir> [--verbs-de N] [--verbs-es N]

Source: Wikidata Lexemes (https://www.wikidata.org/wiki/Wikidata:Lexicographical_data), queried 2026-10-12 through the
public SPARQL endpoint https://query.wikidata.org/sparql (a few paged queries, a descriptive User-Agent, a pause
between requests; QUERIES below, verbatim). Wikidata's structured data, lexemes included, is released under CC0 1.0
(https://creativecommons.org/publicdomain/zero/1.0/, Wikidata:Licensing): no attribution is required, it is given
anyway (LICENSES/Wikidata-CC0.txt). Counts of that date: German 242,105 lexemes (189,032 nouns, 26,873 adjectives,
20,429 verbs; 583,649 verb forms), 7,767 German lexemes with "combines lexemes" (P5238); Spanish 68,285 lexemes (8,574
verbs with 573,450 forms). de_compounds.tsv.gz was rebuilt from a run of 2026-10-17 (0.16.4: the German lemma, parts
and derived queries only; 242,083 German lexemes, 189,034 nouns, 8,402 with P5238); de_es_forms.tsv.gz is of
2026-10-12. Each table's header gives the date its queries ran.

Changes (de_compounds.tsv.gz): German noun, adjective and verb lemmas, folded as text.py folds them (accents dropped,
lower case); every noun of the dictionary is one word unless Wikidata's P5238 analysis says it is a compound (two
readings of one spelling, "Wachstube": left whole) — until 0.11.0 the build also cut nouns with text.py's own
splitter, which read "Tablette" as Tab|Lette and "Weihnachten" as Weih|Nachten —, lexemes derived from another one (P5191) or built with an affix
kept whole, and so are an agent noun of a verb ("Sauger") and the weekdays; for each word a class of how often it is a
part of the other nouns and the linking elements seen after it; compounds whose cuts the splitter would not find by
itself written with their cuts; words the run-time splitter needs neither as a part nor to keep a word (or one of its
forms) whole left out. Changes (de_es_forms.tsv.gz): verb forms folded and lower-cased; kept: single words in Latin
script whose Snowball stem differs from the lemma's, function words too ("fue" in a fact is a form of ir and ser,
whose other forms a question may use); left out: a form spelled like another lexeme with a sense or links (a noun,
an adjective that is not a participle of the verb: "Macht", "vino"), like a form of a noun ("Mächte" / "machte"), or
like a regular form of another such verb ("führen" / "fuhren"); only the commonest verbs of each language, by a
stand-in for frequency (lexemes created by hand before the bulk imports, senses, links from other lexemes)."""
from __future__ import annotations

import collections
import csv
import glob
import gzip
import importlib.util
import math
import sys
import time
import types
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "hermes-plugin-holographic-build/0.11 (https://github.com/itpartypattaya/hermes-plugin-holographic)"
DE, ES = "Q188", "Q1321"
N, V, A = "Q1084", "Q24905", "Q34698"


def _pages(name: str, template: str) -> list[tuple[str, str]]:
    return [(f"{name}_{d}", template.replace("{d}", str(d))) for d in range(10)]


LEMMAS = """# {lang} lexemes (all categories), page {d} of 10 (lexemes whose id ends in {d})
SELECT ?l ?lemma ?cat WHERE {
  ?l dct:language wd:{q} ; wikibase:lemma ?lemma ; wikibase:lexicalCategory ?cat .
  FILTER(STRENDS(STR(?l), "{d}"))
}"""
FORMS = """# {lang} verb forms, page {d} of 10 (lexemes whose id ends in {d})
SELECT ?l ?lemma (GROUP_CONCAT(DISTINCT ?rep; separator="|") AS ?forms) WHERE {
  ?l dct:language wd:{q} ; wikibase:lexicalCategory wd:Q24905 ; wikibase:lemma ?lemma .
  FILTER(STRENDS(STR(?l), "{d}"))
  ?l ontolex:lexicalForm ?f . ?f ontolex:representation ?rep .
} GROUP BY ?l ?lemma"""
QUERIES = [
    *_pages("lemmas_de", LEMMAS.replace("{lang}", "German").replace("{q}", DE)),
    *_pages("lemmas_es", LEMMAS.replace("{lang}", "Spanish").replace("{q}", ES)),
    *_pages("forms_de", FORMS.replace("{lang}", "de").replace("{q}", DE)),
    *_pages("forms_es", FORMS.replace("{lang}", "es").replace("{q}", ES)),
    ("parts_de", """# German lexemes with "combines lexemes" (P5238): the parts, their categories and the series ordinal (P1545)
SELECT ?l ?lemma ?cat ?part ?partLemma ?partCat ?ord WHERE {
  ?l dct:language wd:Q188 ; wikibase:lemma ?lemma ; wikibase:lexicalCategory ?cat ; p:P5238 ?st .
  ?st ps:P5238 ?part .
  OPTIONAL { ?st pq:P1545 ?ord }
  ?part wikibase:lemma ?partLemma ; wikibase:lexicalCategory ?partCat .
}"""),
    ("derived_de", """# German lexemes with "derived from lexeme" (P5191): a derivation or a borrowing, not a compound of two German words
SELECT ?l ?lemma (COUNT(?src) AS ?n) WHERE {
  ?l dct:language wd:Q188 ; wikibase:lemma ?lemma ; wdt:P5191 ?src .
} GROUP BY ?l ?lemma"""),
    *[(f"incoming_{c}", f"""# {name} lexemes used as a part (P5238) or a source (P5191) of other {name} lexemes, with how many
SELECT ?t (COUNT(DISTINCT ?x) AS ?n) WHERE {{
  ?x dct:language wd:{q} ; wdt:P5238|wdt:P5191 ?t .
  ?t dct:language wd:{q} .
}} GROUP BY ?t""") for c, name, q in (("de", "German", DE), ("es", "Spanish", ES))],
    *[(f"senses_{c}", f"""# {name} verbs: number of senses and of sense translations (P5972), a stand-in for how common a verb is
SELECT ?l (COUNT(DISTINCT ?s) AS ?senses) (COUNT(DISTINCT ?tr) AS ?trans) WHERE {{
  ?l dct:language wd:{q} ; wikibase:lexicalCategory wd:Q24905 ; ontolex:sense ?s .
  OPTIONAL {{ ?s wdt:P5972 ?tr }}
}} GROUP BY ?l""") for c, name, q in (("de", "German", DE), ("es", "Spanish", ES))],
]
# Built from the results of the queries above (candidates of the forms filter), 400 lexemes a query:
NOUN_FORMS = """# {lang} nouns one of whose forms may be spelled like a verb form: their forms (verifies a filter)
SELECT ?l (GROUP_CONCAT(DISTINCT ?rep; separator="|") AS ?forms) WHERE {
  VALUES ?l { {ids} }
  ?l ontolex:lexicalForm ?f . ?f ontolex:representation ?rep .
} GROUP BY ?l"""
HOMOGRAPHS = """# {lang} lexemes spelled like a verb form: senses and sense translations, a stand-in for how common
SELECT ?l (COUNT(DISTINCT ?s) AS ?senses) (COUNT(DISTINCT ?tr) AS ?trans) WHERE {
  VALUES ?l { {ids} }
  OPTIONAL { ?l ontolex:sense ?s . OPTIONAL { ?s wdt:P5972 ?tr } }
} GROUP BY ?l"""


def _text_module():
    """text.py of this repository (its fold, stems, function words and the compound splitter the table is for)."""
    pkg = types.ModuleType("_holographic_build")
    pkg.__path__ = [str(ROOT)]
    sys.modules["_holographic_build"] = pkg
    spec = importlib.util.spec_from_file_location("_holographic_build.text", ROOT / "text.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_holographic_build.text"] = module
    spec.loader.exec_module(module)
    return module


T = _text_module()


def fold(word: str) -> str:
    return T.fold(word.lower())


def latin(word: str) -> bool:
    return word.isalpha() and all(T.word_script(c) == "latin" for c in word)


def lid(uri: str) -> int:
    return int(uri.rsplit("/L", 1)[1])


def rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# -- fetch -----------------------------------------------------------------------------------------------------------

def _post(query: str) -> bytes:
    data = urllib.parse.urlencode({"query": query}).encode()
    for attempt in range(5):
        req = urllib.request.Request("https://query.wikidata.org/sparql", data=data,
                                     headers={"Accept": "text/csv", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            wait = int(exc.headers.get("Retry-After") or 30 * (attempt + 1))
            print(f"  HTTP {exc.code}, waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("the endpoint kept refusing")


def fetch(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, query in QUERIES:
        (out / f"{name}.csv").write_bytes(_post(query))
        print(name, file=sys.stderr)
        time.sleep(5)
    for code, lang, (nouns, homographs) in (("de", "German", candidates(out, "de")), ("es", "Spanish", candidates(out, "es"))):
        for kind, template, ids in (("nounform", NOUN_FORMS, nouns), ("homograph", HOMOGRAPHS, homographs)):
            for n, i in enumerate(range(0, len(ids), 400)):
                query = template.replace("{lang}", code).replace("{ids}", " ".join("wd:" + x for x in ids[i:i + 400]))
                (out / f"{kind}_{code}_{n}.csv").write_bytes(_post(query))
                time.sleep(5)


# -- shared loading --------------------------------------------------------------------------------------------------

def lexemes(dl: Path, code: str) -> dict:
    """folded Latin lemma -> {categories} and lexeme ids, of every lexeme of the language."""
    cats, ids = collections.defaultdict(set), collections.defaultdict(set)
    for f in sorted(dl.glob(f"lemmas_{code}_*.csv")):
        for r in rows(f):
            w = fold(r["lemma"])
            if latin(w):
                cats[w].add(r["cat"].rsplit("/", 1)[1])
                ids[w].add(r["l"].rsplit("/", 1)[1])
    return cats, ids


def verb_forms(dl: Path, code: str) -> dict:
    """lexeme id -> (folded Latin lemmas, folded forms): a lexeme may have a lemma in another script too."""
    lex = collections.defaultdict(lambda: (set(), set()))
    for f in sorted(dl.glob(f"forms_{code}_*.csv")):
        for r in rows(f):
            lemmas, forms = lex[r["l"].rsplit("/", 1)[1]]
            w = fold(r["lemma"])
            if latin(w):
                lemmas.add(w)
            forms.update(fold(x) for x in r["forms"].split("|"))
    return lex


# -- German compounds ------------------------------------------------------------------------------------------------

LINK_CODE = {v: k for k, v in T._DE_LINKS.items()}
JUDGE = dict(need=(3, 3, 2), head_need=(4, 3, 2), need_verb=3)  # the dictionary's own words: stricter than run time
WEEKDAYS = frozenset("montag dienstag mittwoch donnerstag freitag samstag sonnabend sonntag".split())


def gold(dl: Path) -> dict:
    """folded compound -> its readings by P5238, each a tuple of (part, start) in order; content words only."""
    content = {N: "N", V: "V", A: "A"}
    parts, info = collections.defaultdict(list), {}
    for r in rows(dl / "parts_de.csv"):
        info[r["l"]] = (r["lemma"], r["cat"].rsplit("/", 1)[1])
        parts[r["l"]].append((r["partLemma"], r["partCat"].rsplit("/", 1)[1]))
    out = collections.defaultdict(list)
    for lexeme, ps in parts.items():
        lemma, cat = info[lexeme]
        w = fold(lemma)
        if not latin(w) or cat not in content or any(c not in content for _, c in ps):
            continue
        found = {}
        for p, c in ps:
            p = fold(p)
            if not latin(p):
                found = {}
                break
            start = -1
            for cand in (p, p.replace("ß", "ss"), T._de_vstem(p) if c == V else "", p[:-1]):
                if len(cand) >= 2 and (start := w.find(cand)) >= 0:
                    break
            if start >= 0 and (start not in found or len(p) > len(found[start])):
                found[start] = p  # spelling variants of one part (Fuß, Fuss) start at one place
        reading = tuple(sorted((s, p) for s, p in found.items()))
        if len(reading) >= 2 and reading[0][0] == 0 and reading not in out[w]:
            out[w].append(reading)
    return out


def affixed(dl: Path) -> set:
    """Lexemes derived from another (P5191), or whose P5238 parts hold an affix and fewer than two words: one word."""
    out = {fold(r["lemma"]) for r in rows(dl / "derived_de.csv")}
    parts, lemma = collections.defaultdict(list), {}
    for r in rows(dl / "parts_de.csv"):
        parts[r["l"]].append(r["partCat"].rsplit("/", 1)[1])
        lemma[r["l"]] = fold(r["lemma"])
    out |= {lemma[x] for x, cs in parts.items() if any(c not in (N, V, A) for c in cs) and sum(c in (N, V, A) for c in cs) < 2}
    return out


def cls_of(n: float) -> int:
    return 0 if n <= 0 else 1 if n < 5 else 2 if n < 20 else 3 if n < 100 else 4


def build_compounds(dl: Path, exclude=frozenset(), log=print) -> tuple[dict, dict]:
    cats, _ = lexemes(dl, "de")
    nouns = {w for w, c in cats.items() if N in c and w not in exclude}
    adjs = {w for w, c in cats.items() if A in c and w not in exclude}
    verbs = {w for w, c in cats.items() if V in c and w not in exclude}
    prefixes = {w.strip("-") for f in dl.glob("lemmas_de_*.csv") for r in rows(f) if r["cat"].endswith("/Q134830")
                for w in [fold(r["lemma"])]}
    bound = {p for p in prefixes if latin(p) and len(p) >= 3 and p not in nouns and p not in adjs}  # tele-, trans-
    analyses = {w: r for w, r in gold(dl).items() if w not in exclude}
    one_word = (affixed(dl) | WEEKDAYS) - set(exclude)

    def table(prod, links, noun_set):
        out = {}
        for w in (noun_set | adjs | verbs) - bound:
            kinds = "".join(k for k, s in (("N", noun_set), ("A", adjs), ("V", verbs)) if w in s)
            out[w] = (kinds, cls_of(prod.get(w, 0)), links.get(w) if "N" in kinds else None)
        return out

    # 1. how often a word is a part of a two-part reading of another noun (a noun or adjective reading of a modifier
    # takes the credit before a verb's stem; a part in P5238 counts 3), and the linking elements after a modifier
    flat = {w: (k, 4, None) for w, (k, _, _) in table({}, {}, nouns).items()}
    two = T._DeSplitter(flat, {}, need=(0, 0, 0), need_verb=0, max_parts=2)
    prod = collections.Counter()
    seen = collections.defaultdict(collections.Counter)
    for w in nouns:
        if len(w) < 7:
            continue
        by_cut = collections.defaultdict(list)
        for seg in two.segmentations(w):
            by_cut[seg[0][0]].append(seg)
        for surface, segs in by_cut.items():
            mods = {s[0][1] for s in segs if s[0][3] != "V"} or {s[0][1] for s in segs}
            heads = {s[1][1] for s in segs}
            for m in mods:
                prod[m] += 1 / len(mods)
            for h in heads:
                prod[h] += 1 / len(heads)
        if len(by_cut) == 1:  # one place to cut: its linking element is evidence
            surface, segs = next(iter(by_cut.items()))
            for lemma in {s[0][1] for s in segs if s[0][3] == "N"}:
                if surface == lemma[:-1]:
                    seen[lemma]["d"] += 1
                elif surface.startswith(lemma) and surface[len(lemma):] in LINK_CODE:
                    seen[lemma][LINK_CODE[surface[len(lemma):]]] += 1
    for readings in analyses.values():
        for _, p in readings[0]:
            prod[p] += 3
    links = {w: "".join(sorted(obs)) for w, obs in seen.items() if sum(obs.values()) >= 3}
    # 2. every noun of the dictionary: a compound (cuts) or one word. Wikidata's analysis wins where it has one; a
    # lexicalised compound text.py keeps whole (_DE_ONE_WORD: "Hochzeit", "Tablette") is one word here too; the rest is
    # judged with text.py's splitter under stricter limits than at run time. 0.12.0 made every noun without an analysis
    # one word instead, to keep lexicalised compounds whole; the table it built was never shipped: Wikidata lists most
    # compounds without an analysis, and 15 of 33 everyday ones were no longer split (Zahnarzttermin, Schulbus,
    # Mietvertrag, Elternabend; 0.16.4).
    judge = T._DeSplitter(table(prod, links, nouns), {}, **JUDGE)
    cuts, atoms, why = {}, set(), collections.Counter()
    for w in nouns:
        if w in analyses:
            readings = analyses[w]
            cuts[w] = frozenset(s for s, _ in readings[0][1:]) if len(readings) == 1 else frozenset()
            why["Wikidata's analysis" if len(readings) == 1 else "two analyses: whole"] += 1
        elif any(w.endswith(end) and (w[:len(w) - len(end)] if end else w) in T._DE_ONE_WORD for end in T._DE_NOUN_END):
            atoms.add(w)
            why["lexicalised (text.py _DE_ONE_WORD): whole"] += 1
        elif w in one_word or (w.endswith("er") and (w[:-2] + "en" in verbs or w[:-2] + "n" in verbs)):
            atoms.add(w)
            why["derived, affixed, agent noun, weekday: whole"] += 1
        elif len(w) < 6 or not (seg := judge.choose(w)):
            atoms.add(w)
            why["no analysis: one word"] += 1
        elif prod.get(w, 0) >= math.exp(sum(math.log(1 + prod.get(p[1], 0)) for p in seg) / len(seg)) - 1:
            atoms.add(w)  # Koehn & Knight: as often a part itself as its parts are
            why["a part as often as its parts: one word"] += 1
        else:
            cuts[w] = judge.cuts(seg)
            why["judged a compound"] += 1
    # the linking elements of a compound modifier belong to its last word (Hochzeit+s -> Zeit+s)
    for w, c in cuts.items():
        if c and w in links:
            head = w[max(c):]
            for h in (head, head[:-1]):
                if h in atoms:
                    if h in links:
                        links[h] = "".join(sorted(set(links[h]) | set(links[w])))
                    break
    # a compound's parts that are compounds themselves are cut too (Zahnarzt|termin -> Zahn|arzt|termin)
    fine: dict = {}

    def refine(w, depth=0):
        if w not in fine:
            out = set(cuts.get(w, ()))
            bounds = sorted(out)
            for a, b in zip([0, *bounds], [*bounds, len(w)]):
                part = w[a:b]
                for cand in (part, part[:-1], part[:-2], part[:-3], part + "e"):
                    if len(cand) >= 6 and cuts.get(cand) and depth < 3:
                        out |= {a + x for x in refine(cand, depth + 1) if a + x < b}
                        break
            fine[w] = frozenset(out)
        return fine[w]

    cuts = {w: refine(w) for w in cuts}
    runtime = T._DeSplitter(table(prod, links, atoms), {})
    over = {}
    for w, c in cuts.items():
        got = runtime.split(w)
        if (runtime.cuts(got) if got else frozenset()) != c:
            over[w] = tuple(sorted(c))
    runtime.cuts_of = over
    # 3. leave out what the run-time splitter needs neither as a part nor to keep a word or its forms whole
    forms = collections.defaultdict(set)
    for lemmas, fs in verb_forms(dl, "de").values():
        for lemma in lemmas:
            forms[lemma] |= {f for f in fs if latin(f)}
    rt = runtime

    def usable(w, kinds, c):
        if "N" in kinds and (rt._ok(w, c, rt.need) or rt._ok(w, c, rt.head_need) or w in T._DE_TWO_LETTER_HEADS):
            return True
        return ("A" in kinds and rt._ok(w, c, rt.need)) or \
            ("V" in kinds and c >= rt.need_verb and len(T._de_vstem(w)) >= rt.min_part)

    dropped = 0
    for w in sorted(rt.words):
        kinds, c, _ = rt.words[w]
        if usable(w, kinds, c):
            continue
        variants = {w} | ({w + e for e in T._DE_NOUN_END + T._DE_FEM_END} if "N" in kinds else set()) | \
            ({w + e for e in T._DE_ADJ_END} if "A" in kinds else set()) | (forms.get(w, set()) if "V" in kinds else set())
        entry = rt.words.pop(w)
        if any(rt.split(v) for v in variants if len(v) >= rt.min_word):
            rt.words[w] = entry
        else:
            dropped += 1
    log(f"German nouns: {dict(why)}; table {len(rt.words)} words, {len(over)} written with their cuts, "
        f"{dropped} left out")
    return rt.words, over


# -- German and Spanish verb forms -----------------------------------------------------------------------------------

FAMILY = 1.0  # weight of the family of prefixed verbs in the stand-in for frequency
NOUN_ENDINGS = {"de": ("e", "en", "n", "er", "s", "es", "ern", "ens", "nen"), "es": ("s", "es")}
PARTICIPLE = {"de": lambda f: f.endswith(("t", "en")),
              "es": lambda f: f.endswith(("ado", "ada", "ados", "adas", "ido", "ida", "idos", "idas", "to", "ta",
                                          "tos", "tas", "cho", "cha", "so", "sa"))}


def candidates(dl: Path, code: str) -> tuple[list, list]:
    """Lexeme ids to look up: nouns one of whose forms may be a verb form, lexemes spelled like a verb form."""
    cats, ids = lexemes(dl, code)
    noun_ids = {w: {i for i in ids[w]} for w, c in cats.items() if N in c}
    nouns, homographs = set(), set()
    for lemmas, forms in verb_forms(dl, code).values():
        for lemma in lemmas:
            own = T.stem(lemma, code)
            for f in forms:
                if latin(f) and T.stem(f, code) != own:
                    for e in NOUN_ENDINGS[code]:
                        if f.endswith(e) and f[:-len(e)] in noun_ids:
                            nouns |= noun_ids[f[:-len(e)]]
                    homographs |= ids.get(f, set())
    return sorted(nouns), sorted(homographs | nouns)  # senses are asked for the nouns too


def build_forms(dl: Path, code: str, top: int | None, log=print) -> list[str]:
    stem = lambda w: T.stem(w, code)  # noqa: E731
    cats, ids = lexemes(dl, code)
    lex = verb_forms(dl, code)
    incoming = {lid(r["t"]): int(r["n"]) for r in rows(dl / f"incoming_{code}.csv")}
    senses = {lid(r["l"]): int(r["senses"]) + int(r["trans"]) for f in sorted(dl.glob(f"homograph_{code}_*.csv"))
              for r in rows(f)}
    verb_senses = {lid(r["l"]): int(r["senses"]) + int(r["trans"]) for r in rows(dl / f"senses_{code}.csv")}
    noun_forms = collections.defaultdict(set)  # form -> nouns that have it
    for f in sorted(dl.glob(f"nounform_{code}_*.csv")):
        for r in rows(f):
            for x in r["forms"].split("|"):
                noun_forms[fold(x)].add(r["l"].rsplit("/", 1)[1])
    early = sorted(int(i[1:]) for i in lex)[300]  # the first lexemes, made by hand before the bulk imports

    def real(lexeme_id: str) -> bool:
        """A lexeme someone described: a sense, a link from another lexeme, or one of the first ones made."""
        i = int(lexeme_id[1:])
        return senses.get(i, 0) > 0 or verb_senses.get(i, 0) > 0 or incoming.get(i, 0) > 0 or i <= early

    regular_of = collections.defaultdict(set)  # form -> verbs (with an id) it is a same-stem form of
    for lexeme_id, (lemmas, forms) in lex.items():
        for lemma in lemmas:
            own = stem(lemma)
            for f in forms:
                if latin(f) and stem(f) == own:
                    regular_of[f].add((lemma, lexeme_id))
    # how many other verbs end in this one (gehen: ausgehen, vergehen …; poner: componer, suponer …): a common verb
    # has a large family of prefixed ones
    verbs = {lemma for lemmas, _ in lex.values() for lemma in lemmas}
    family = collections.Counter(v[i:] for v in verbs for i in range(2, len(v) - 2) if v[i:] in verbs)
    score = {}
    for lexeme_id, (lemmas, _) in lex.items():
        i = int(lexeme_id[1:])
        for lemma in lemmas:
            s = (3 if i <= early else 0) + (2 if verb_senses.get(i) else 0) + math.log(1 + incoming.get(i, 0)) + \
                FAMILY * math.log(1 + family[lemma])
            score[lemma] = max(score.get(lemma, (-1, 0)), (s, -i))
    keep = set(sorted(score, key=score.get, reverse=True)[:top]) if top else set(score)
    entries, why = collections.defaultdict(set), collections.Counter()
    for lexeme_id, (lemmas, forms) in lex.items():
        for lemma in lemmas & keep:
            own = stem(lemma)
            for f in forms:
                if not latin(f) or stem(f) == own:
                    continue
                other = [i for i in ids.get(f, ()) if i != lexeme_id and real(i)]
                if other and not (cats[f] == {A} and PARTICIPLE[code](f)):  # "verloren": the participle's own
                    why["spelled like another word"] += 1
                elif any(real(i) for i in noun_forms.get(f, ())):
                    why["spelled like a form of a noun"] += 1
                elif any(v != lemma and real(i) for v, i in regular_of.get(f, ())):
                    why["spelled like a regular form of another verb"] += 1
                else:
                    entries[lemma].add(f)
    n = sum(map(len, entries.values()))
    log(f"{code} verb forms: {n} forms of {len(entries)} verbs (top {top or 'all'} of {len(score)}); left out "
        f"{dict(why)}")
    return [f"{code}\t{lemma}\t{' '.join(sorted(fs))}" for lemma, fs in sorted(entries.items())]


# -- output ----------------------------------------------------------------------------------------------------------

HEAD = ("# {what}, derived from Wikidata Lexemes (https://www.wikidata.org/wiki/Wikidata:Lexicographical_data), queried\n"
        "# {date} through https://query.wikidata.org/sparql. Wikidata's data is released under CC0 1.0 (public domain\n"
        "# dedication, https://creativecommons.org/publicdomain/zero/1.0/): no attribution is required; it is given anyway.\n"
        "# Changes: see tools/build_de_es_lexicon.py, which made this file. See LICENSES/Wikidata-CC0.txt.\n# {fmt}\n")


def queried(dl: Path) -> str:
    """The date the downloads in *dl* were made (UTC), for a table's header."""
    return time.strftime("%Y-%m-%d", time.gmtime(max(f.stat().st_mtime for f in dl.glob("*.csv"))))


def write(path: Path, header: str, lines: list[str]) -> int:
    data = gzip.compress((header + "\n".join(lines) + "\n").encode("utf-8"), 9, mtime=0)
    path.write_bytes(data)
    return len(data)


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    mode, dl = argv[1], Path(argv[2])
    if mode == "fetch":
        fetch(dl)
        return
    top = {c: int(argv[argv.index(f"--verbs-{c}") + 1]) if f"--verbs-{c}" in argv else None for c in ("de", "es")}
    words, over = build_compounds(dl)
    lines = [f"{w}\t{k}{c}{l or ''}" for w, (k, c, l) in sorted(words.items())]
    lines += [f"{w}\t={','.join(map(str, c))}" for w, c in sorted(over.items())]
    size = write(ROOT / "dictionaries" / "de_compounds.tsv.gz", HEAD.format(
        what="German compound dictionary for text.py's splitter", date=queried(dl),
        fmt="word TAB kinds (N noun, A adjective, V verb) class (0-4) [linking elements] | word TAB =cut,cut"), lines)
    print(f"de_compounds.tsv.gz: {size} bytes")
    lines = build_forms(dl, "de", top["de"]) + build_forms(dl, "es", top["es"])
    size = write(ROOT / "dictionaries" / "de_es_forms.tsv.gz", HEAD.format(
        what="German and Spanish verb forms whose Snowball stem differs from their lemma's", date=queried(dl),
        fmt="language TAB lemma TAB forms"), lines)
    print(f"de_es_forms.tsv.gz: {size} bytes")


if __name__ == "__main__":
    main(sys.argv)
