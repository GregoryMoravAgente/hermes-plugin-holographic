"""Build en_derivation.tsv.gz from Open English Wordnet (WN-LMF XML, e.g. english-wordnet-2025.xml.gz): the
sense-level "derivation" relations (and "pertainym", which links an adjective or adverb to the word it comes from),
projected to pairs of single words, both as Snowball (Porter2) English stems, kept when their shape is a derivation.

    python build_en_derivation.py <english-wordnet-YYYY.xml.gz> <out.tsv.gz> [--mode shape|all] [--dump rejected.tsv]

Kept when the derived (longer) word's commonest sense carries the link (not "butter" / "butt": butter is food first),
and when neither stem is another, unrelated word of the dictionary ("pearly" stems to "pear").
shape: the shorter stem is a beginning of the longer one (a three-letter stem only by a common suffix), or the two
share all but the last two letters of the shorter one, and at least three (decid / decis, explain / explan,
receiv / receipt, anger / angri).
all: every link whose two words have different stems (for comparison only)."""
import gzip
import re
import sys
import xml.etree.ElementTree as ET

import snowballstemmer

st = snowballstemmer.stemmer("english")
src, dst = sys.argv[1], sys.argv[2]
mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "shape"
dump = sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv else ""
RELATIONS = ("derivation", "pertainym")
WORD = re.compile(r"^[a-z]+$")
SHORT_EXT = ("er", "or", "ist", "ian", "al", "ic", "y", "ish", "ful", "less", "ness", "ment", "ion", "ance", "ence",
             "ive", "ous", "ent", "ant", "ism", "ity", "en", "ly", "ship", "hood", "dom", "ery", "ary", "ing", "ed",
             "i", "yer", "iz", "it", "ifi", "ster")  # Porter2 writes a final -y as -i: fat / fatti, fun / funni

# sense id -> (lemma, is the first sense of its word in that part of speech); senses are listed most frequent first,
# and a word's homographs (tower: a building / one who tows) come as separate entries, the commonest first
sense_info, links, version, lemmas = {}, [], "", set()
lemma, pos, current, first = "", "", "", set()
with gzip.open(src, "rb") as fh:
    for event, el in ET.iterparse(fh, events=("start", "end")):
        if event == "start":
            if el.tag == "Lexicon":
                version = el.get("version", "")
            elif el.tag == "Lemma":
                lemma, pos = el.get("writtenForm", ""), el.get("partOfSpeech", "")
                lemmas.add(lemma.lower())
            elif el.tag == "Sense":
                current = el.get("id")
                sense_info[current] = (lemma, (lemma.lower(), pos) not in first)
                first.add((lemma.lower(), pos))
            elif el.tag == "SenseRelation" and el.get("relType") in RELATIONS:
                links.append((current, el.get("target"), el.get("relType")))
        elif el.tag == "LexicalEntry":
            el.clear()
linked = {frozenset((sense_info[a][0].lower(), sense_info[b][0].lower())) for a, b, _ in links if b in sense_info}


def shape_ok(a, b):
    s, l = sorted((a, b), key=len)
    if len(s) < 3 or s == l:
        return False
    if l.startswith(s):
        extra = l[len(s):]
        return any(extra == x or extra == s[-1] + x for x in SHORT_EXT) if len(s) == 3 else len(extra) <= 8
    common = 0
    while common < len(s) and s[common] == l[common]:
        common += 1
    return common >= 3 and common >= len(s) - 2 and len(l) - common <= 8


def homograph(word, stem_, other):
    """The stem is another word of the dictionary, unrelated to the pair's other word: "pearly" stems to "pear"."""
    return stem_ != word and stem_ in lemmas and frozenset((stem_, other)) not in linked


pairs, rejected = set(), set()
for source, target, rel in links:
    if target not in sense_info:
        continue
    (a, a_first), (b, b_first) = sense_info[source], sense_info[target]
    a, b = a.lower(), b.lower()
    if not (WORD.match(a) and WORD.match(b)):
        continue
    x, y = st.stemWord(a), st.stemWord(b)
    if x == y:
        continue
    pair = tuple(sorted((x, y)))
    # the derived word must mean the derivation in its commonest sense: "butter" is food before it is one who butts,
    # "tower" a building before one who tows. A pertainym's source is the derived word ("medical" -> "medicine"); in a
    # derivation link it is the longer one
    derived_first = a_first if rel == "pertainym" or len(x) > len(y) else b_first if len(y) > len(x) else         a_first or b_first
    if mode == "all" or (shape_ok(x, y) and derived_first and not homograph(a, x, b) and not homograph(b, y, a)):
        pairs.add(pair)
    else:
        rejected.add((pair, a, b))
header = (f"# English derivation pairs (Snowball English stems), derived from Open English Wordnet {version}\n"
          "# (https://en-word.net/, https://github.com/globalwordnet/english-wordnet) (c) The Open English Wordnet\n"
          "# Team, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), based on Princeton WordNet 3.1, WordNet 3.1\n"
          "# Copyright 2011 by Princeton University. All rights reserved. Modified: sense-level derivation and pertainym\n"
          "# relations projected to pairs of single words, both reduced to Snowball stems, kept when their shape is a\n"
          "# derivation, the derived word's commonest sense\n"
          "# carries the link and no stem is an unrelated word, deduplicated (tools/build_en_derivation.py). See LICENSES/OEWN-CC-BY-4.0.md and\n"
          "# LICENSES/OEWN-WNDB-License.txt.\n")
out = (header + "\n".join(f"{a}\t{b}" for a, b in sorted(pairs)) + "\n").encode("utf-8")
open(dst, "wb").write(gzip.compress(out, 9, mtime=0))
print(len(links), "links;", len(pairs), "pairs kept;", len({r[0] for r in rejected}), "rejected;",
      len(gzip.compress(out, 9, mtime=0)), "bytes gz")
if dump:
    with open(dump, "w", encoding="utf-8") as fh:
        for pair, a, b in sorted(rejected):
            fh.write(f"{pair[0]}\t{pair[1]}\t{a}\t{b}\n")
