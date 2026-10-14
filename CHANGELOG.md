# Changelog

## 0.14.1 — 2026-10-14

### Fixed

- **Fact vectors were held to the message's timeout.** 0.14.0 gave every request `embedding_timeout` (3 s), the bound
  meant for the one message a turn waits for; a batch of 32 facts may take longer (on its first call, a real store's
  did), and then no fact got a vector and search by meaning never started. Fact vectors, made after the turn where
  nobody waits, now share the 8-second after-turn budget — with every wait for the provider lock, taken with a timeout
  and a second kept for storing — and the message keeps `embedding_timeout`. Decoding the answer runs inside the
  request's own wait, so it is bounded too. Reviewed by Codex CLI.

### Measured on a real store

A family assistant's store (58 facts, mostly Russian, every fact with keywords from hermes-dreaming's nightly job), on
a copy; questions written by a model from the facts; numbers only; the run counted only with no failed request:

| | Words only | + meaning (text-embedding-3-small) |
|---|---|---|
| Natural questions: the fact recalled before the turn | 57 / 58 | 57 / 58 |
| Sharing no word with their fact but the person's name | 7 / 27 | 8 / 27 |
| Sharing no word at all | 0 / 12 | 0 / 12 |
| A wrong fact recalled by meaning | — | 0 |
| Chit-chat, off-topic, someone not stored: a fact by meaning | — | 0 / 30 |

On this store meaning added one recall in these questions, with nothing wrong observed; keywords and names already
found most. The synthetic benchmark in the README (facts without keywords: 22 → 54 of 67) is a separate measurement.

## 0.14.0 — 2026-10-14

### Added

- **Search by meaning — optional, online, off by default** (`embeddings: on`). An OpenAI-compatible embeddings API you
  name (OpenAI, Gemini, or a local server such as Ollama) finds facts a question words differently: "Which machine
  copies our data while we sleep?" recalls "The nightly backup runs from the OES box at 2 am." Words keep the
  recall: meaning reorders the facts that share words with the message and may add one more, after them and marked
  `(by meaning)`, only when it stands far out from the rest of the store for that message and, by a heuristic,
  mentions whoever the message names (capitalized words inside a sentence, known entities); `fact_store search` adds the closest facts by meaning after the word matches. Fact vectors are made
  after the turn on the core's background worker, never inside a write; every failure falls back to words and leaves
  the endpoint alone for a while. Facts recall would not inject, and facts and messages that look like they hold a
  secret (a credential with its value, `OPENAI_API_KEY=…`, an Authorization header, common key prefixes, an encoded
  blob — by pattern, best effort), are not sent; `forget` deletes the vector; after switching off, the next writable
  session deletes them all. A separate module (`embeddings.py`) with no new dependency. Measured on a synthetic set of 100 facts in five languages: paraphrases recalled before the turn went
  from 22 of 67 to 54 (text-embedding-3-small) and 59 (gemini-embedding-001), chit-chat recalled nothing. See the
  README for what is sent, to whom, the cost and the limits. Design after
  [#3](https://github.com/itpartypattaya/hermes-plugin-holographic/issues/3) by njwangfeng; our differences: vectors in
  their own table keyed by a hash of their text and by model, address and an optional label of your own (older
  versions, edits and another server or deployment never meet a stale vector; a server that swaps the model behind
  the same address and name is caught only by changing the label), no network call inside a database write (fact vectors are requested outside the
  provider lock; a message's vector, before the turn, within it), a bar that rises with the store, and the name rule.
  Two reviews by Codex CLI before release found, and this release fixes: a message holding a key in a shape the
  credential check did not know; a key with a line break that could reach the log; a vector written after its store
  was closed; two servers sharing a model name, or two paths differing in case; answers with wrong indexes, sizes or
  values whose squares overflow; a category filter applied after the cut; a public IPv6 address taken for a local
  one; a replaced vector kept from a cache; vectors of facts an older version deleted left while another process held
  the file; a timeout that bounded each read but not the request (a server sending its headers or chunks a byte at
  a time held the caller far longer — the request now runs on a thread of its own); a catch-up that could start a
  request after its budget was spent; deployments chosen by the query string sharing vectors. With it off, scores and recall are pinned to
  0.13.0's by a test.

### Changed

- **The system-prompt block holds no live number.** "Active: N facts in a local store." changed with every stored
  fact, and a prompt cache that reuses the longest identical prefix re-read everything after it at each new session
  (about 21K tokens in one deployment). It now says "Active: facts are kept in a local store."; empty / not empty
  still flips once per store. After [adurham's fork](https://github.com/adurham/hermes-plugin-holographic) (220292a).

## 0.13.0 — 2026-10-13

### Added

- **Recall checks a fact again on the way out.** The filter of what a fact must not be ran only when a fact was
  written, so a fact stored before a rule existed (a credential before 0.12.0), by an older release or by another
  program writing the file was injected into every turn it matched. Before recall, a fact is checked again for
  chat-role or tool-call markup, instruction-like phrasing, a credential with its value, invisible characters and
  images; such a fact is left out of recall and logged once per process. `fact_store`
  search, list and history still show it — the agent asked, and can change or forget it. Length, JSON and blobs
  are not checked on the way out: they make a poor fact, not a dangerous one. (hermes-agent#136997 asked for this
  in another provider.)
- **An image is not a fact** — markdown (`![x](https://host/p.png?d=…)`, `![x][1]`) or HTML (`<img src=…>`): a
  client that renders it fetches the address by itself, and data can ride in the path as well as in the query.

### Fixed

- The credential rule of 0.12.0 read at most two words between the credential word and its value
  ("password for the garden shed is …" passed); up to three now, each length tried on its own so a later word
  never stands in for the value. The value may follow a colon without a space (`password:Qwerty123`). Fewer false
  alarms: a decimal number is a price, not a PIN (`the token is 0.125 USD`), and twelve letters of one lower-case
  word are not a secret (`API key authentication is enabled`). Reviewed by Codex CLI before release.

## 0.12.0 — 2026-10-13

Fixes from an audit of 0.6.2–0.11.0: a write transaction that could stay open after a failed COMMIT, `forget`
waiting on readers, credentials in extracted facts, and the language layer (names with `ı` / `ł`, a damaged
Chinese dictionary, stems of an undetermined language, lexicalised German compounds, Japanese kanji folded as
Chinese, Spanish `va`).

### Fixed — store

- **A failed COMMIT no longer leaves the transaction open.** `_tx` ran COMMIT outside its guard: in rollback-journal
  mode (the core keeps DELETE on a SQLite it deems WAL-reset-vulnerable, and on network file systems) a reader in
  another process makes COMMIT fail with "database is locked" after the busy timeout, and the open transaction
  swallowed every later write of the process — nothing was committed again until it exited. COMMIT is inside the
  guard now; a failure rolls back, drops the caches and raises. (WAL files, the usual case, were never affected.)
- **`forget` does not wait for a foreign reader.** Its VACUUM and WAL checkpoint ran with the connection's 10 s
  busy timeout under the provider lock, so a reader in another process (a backup, the nightly dreaming pass)
  stalled every memory operation for about 11 s. Both run with busy timeout 0 now: the scrub comes back as
  `pending` at once and is retried at the next open or forget, as before.
- **Credentials are never stored.** A credential word with a value next to it (`password hunter2`, `пароль от
  wifi: Qwerty123`, `API key sk-…`, `pin code 4821`) is refused by auto-extraction and by `fact_store` add /
  change / correct, with the reason. The word alone is still a fact (`I use a password manager`).
- **Short Chinese, Japanese and Korean sentences are extracted**: the ten-character floor was Latin-tuned
  (`我们决定用Go。` is eight); a sentence with CJK characters needs six.
- A dot between a lower-case and a capital letter ends a sentence (`tea.I use` — a missing space; `Python 3.12`,
  `topic.md` unchanged). `revert` after a revert says why there is nothing to revert. `scope: user` falls back to
  `user_id_alt` when the platform gives no `user_id`. A database written by a newer release (`meta.history_schema`
  above the known one) is not opened for writing — the migration would have stamped the schema back; readers read.
- Docs: the tombstone digest is specified for consumers (`meta.tombstone_key`, HMAC-SHA256, the normalisation);
  the `-<sha256[:10]>` suffix of a sanitised or cut file name; which contexts migrate; config is read at start.

### Fixed — languages

- **Names with `ı` or `ł` are found from text of any language.** The as-written form (what FTS5's unicode61
  indexes: `ışık`, `łukasz`) was added to a word's keys only in Turkish, Polish, Italian or Dutch text; an English
  "Who is Işık?" built the prefix query `isik*` and never reached the indexed token. Any Latin text adds it now,
  on both the fact and the question side (`Who is Łukasz?` finds `Łukasz is …`). Typing the name without the
  letter (`Lukasz`) still does not reach the `ł` spelling: that is the index, not this fix.
- **A damaged `zh_words.txt.gz` no longer breaks Chinese.** A file with a valid header and a corrupt stream raised
  `zlib.error` on every Chinese text and was retried on each call (every other loader already caught it). It now
  loads once as no words: pairs-only Chinese, as without the file.
- **Stems of an undetermined language must look like words.** In Latin text of no determined language every
  stemmer's output was a key with no check, so Indonesian's read `media` as me- + `dia` (he, she) and `beer` as
  `be`: "social media" found and recalled every Indonesian fact that says `dia`. A stem of a language the text was
  not determined as is kept only when it has at least the minimum stem length and is not a function word of that
  language. Determined text, and the English / Russian / Spanish / French benches, are unchanged.
- **Lexicalised German compounds stay whole.** `Hochzeit` (a wedding) was split to hoch|zeit, so "Wann hast du
  Zeit?" recalled "Die Hochzeit ist im Juni" at full weight; likewise `Frühstück` (Stück), `Weihnachten` (Nacht),
  `Augenblick`, `Verstand`, `Reisende`, and `Tablette` was cut tab|lette by the build although the comment said
  it stays whole. A list of such words (and their forms) is never split, before the dictionary's own cuts. The
  build script no longer cuts a noun with the splitter's own judgement — only Wikidata's P5238 analysis makes a
  compound; **the shipped `de_compounds.tsv.gz` is not rebuilt yet** (the build needs the SPARQL download; the
  README's held-out numbers still describe the 0.11.0 table) — the list covers the reported cases until then.
- **Japanese kanji are not folded as Traditional Chinese.** The Traditional → Simplified table was applied to every
  Han character, Japanese ones included (書→书, 紙→纸), and a Japanese question met a Chinese fact through a kanji it
  had rewritten (手紙, a letter, vs 手纸). Text with kana keeps its kanji now; the variants table is built the same
  way. A question in kanji only cannot be told from Chinese and is still folded, so a Japanese fact keeps that
  form too (`締切` still meets `締切は明日`); the Chinese-question → Japanese-fact direction through that form
  remains, and needs the question's script in the n-gram match to close.
- **Spanish `va`, `van`, `vas`, `voy` tell the language** without being dropped as function words: "Eva va a la
  escuela" was determined as French / Italian (no Spanish stems, no verb forms); the forms of `ir` now vote for
  Spanish and stay words of the question ("¿Cómo va Javier?" still meets `fue`).

## 0.11.0 — 2026-10-13

Dictionaries for more languages: English words of one root from Open English Wordnet, Japanese spelling variants from
SudachiDict, Chinese words from jieba's dictionary, German compounds and German and Spanish verb forms from Wikidata;
Italian, Turkish, Polish and Dutch at the level of Spanish and French.

### Added

- **Japanese spelling variants (表記揺れ).** One Japanese word is written in kanji or kana, with more or less okurigana,
  or as a loanword with or without the long vowel, and character n-grams could not join the spellings. In text that
  has kana, a variant now compares as its word's representative spelling: `子ども` / `子供`, `引越し` / `引越` /
  `引っ越し`, `申込` / `申し込み`, `打合せ` / `打ち合わせ`, `コンピュータ` / `コンピューター`, `ウィルス` / `ウイルス`,
  `ヴァイオリン` / `バイオリン`, `きょう` / `今日`. Chinese text (no kana) is never touched; stored text keeps its spelling.
  The spellings come from SudachiDict's synonym source (`src/main/text/synonyms.txt`, release v20260723.1, Works
  Applications, Apache-2.0, `LICENSES/SudachiDict-Apache-2.0.txt`): spellings of one lexeme only (variant and
  misspelling rows of one group, lexeme, form type and abbreviation form, with the expansion flag of their
  representative) — no synonyms, abbreviations (`スマホ` stays apart from `スマートフォン`), translations or romaji.
  `ja_variants.tsv.gz` (47 KB, 5,167 variants of 4,011 words); `tools/build_ja_variants.py` rebuilds it. Left out:
  single characters, hiragana of two letters (`いす`, `かに`), hiragana verbs and adjectives Sudachi marks as no
  expansion trigger (homophones: `はなす` is 話す, 離す or 放す), words whose representative is one kanji (`たまご` /
  `卵`: a lone kanji has no bigram, so two facts that both wrote `たまご` would stop meeting), `いつも` / `いつごろ` /
  `だいぶ` (`何時`, `大分` are other words), and any variant inside the filler and greeting lists.
- Japanese has no spaces, so a variant is replaced only where its edges can be word edges, longest first: hiragana
  not inside other hiragana except next to a particle or a suffix (`たち`, `さん`, `だけ` …), and not right after a kanji
  (`べんきょう` holds no `きょう`, nor does `きょうだい`; `白い草` holds no `い草`); katakana not inside a longer katakana
  word (`アイス` holds no `イス`, `カスタマイズ` no `カスタマ`), except variants of six letters or more
  (`セキュリティポリシー`); a representative written as it is stays (`コンピューター` holds no `コンピュータ`). A variant
  with a kanji in it may run into its inflection (`引越しします` → `引っ越しします`). The text is folded again until
  nothing changes, so folded text folds to itself. `_JA_VARIANTS = False` turns it off. Loaded on the first text with
  kana: 0.03 s, about 2 MB.
- **Chinese words (jieba's dictionary).** Chinese has no spaces, and character pairs could not tell a word from the
  halves of two words: `和服` (kimono) was found in `我和服务员` ("me and the waiter"), `研究生` in `研究生命`, `校长` in
  `学校长期`, `市长` in `南京市长江大桥`. Text whose Han is Chinese (no kana, no Hangul) is now also split into words:
  the most probable split by word frequency, as jieba cuts without its HMM (its word graph and route, written anew in
  `text.py`, `zh_words`; a character that begins no word stands alone). The words add to the pairs, they do not
  replace them. A seam is where two dictionary words of two or more characters meet, or one meets a function
  character standing alone; a pair across a seam is written with a separator (`和`␟`服`), so a question's pair inside
  a word is not found where a fact holds it only across a seam, a question's pair across a seam meets only the same
  seam (`几岁 | 了` / `三岁 | 了`), and a character a fact holds only as the half of a question's split pair does not
  count for the character coverage (`研究 | 生命` keeps the `究` of `研究`, not the `生` of `研究生`). Every pair keeps
  its weight, so coverage is counted as in 0.10.0. No seam next to a content character standing alone (`看 | 牙医`
  still holds `看牙`, `续 | 保险` still `续保`). One-character words are searched alone, wherever they stand in a fact
  (`猫` in `猫粮`, `牙` in `牙医`), except function characters, negators, numerals, units of the calendar, the commonest
  (round(4 ln f) ≥ 43 in the dictionary: `过`, `看`, `去`), characters jieba tags as a person name (`王`, `李`, `陈`), and,
  in a question, two side by side (a name the dictionary lacks: `怡 君`; its pair stands for it, as before). The
  rerank's word overlap uses the same pairs. Traditional text is split after folding. Japanese and Korean text, and a
  Japanese question against Chinese facts, are compared exactly as before; `_ZH_WORDS = False` gives 0.10.0's results
  exactly, question by question.
- `zh_words.txt.gz` (1.45 MB) from jieba's `jieba/dict.txt` (commit 67fa2e36e72f69d9134b8a1037b83fbb070b9775, MIT,
  `LICENSES/jieba-MIT.txt`): entries in Han characters only, folded to Simplified with `cjk_fold.py` (entries that fold
  together add up), frequency kept as round(4 ln f), the part of speech dropped except for the single characters
  tagged as a person name; all 347,549 words (10,640 of one character), one line per first character.
  `tools/build_zh_words.py` rebuilds it. Loaded on the first Chinese text: about 0.2 s, 30 MB (41 MB at the peak of
  loading). A copy without the file compares Chinese as before. jieba is not a dependency. Its author has said the
  frequencies were counted on the People's Daily 1998 and MSR segmentation corpora, which carry research terms of
  their own; the dictionary is used as jieba publishes it, under MIT.
- **German compounds are split** (Wikidata Lexemes, CC0, `de_compounds.tsv.gz`, 212 KB; `LICENSES/Wikidata-CC0.txt`;
  `tools/build_de_es_lexicon.py` holds the SPARQL queries, run 2026-10-12). In text determined as German: parts of
  three letters or more with German linking elements, the analysis with the fewest parts, Wikidata's own analysis
  (P5238) where it has one; two analyses that cut differently leave the word whole unless one is clearly likelier
  (`Staubecken`, `Wachstube`). Both directions: a fact's compound is found by its last part, at full weight; a
  question's compound finds a fact that writes its parts apart. Earlier parts, and a last part of three letters
  (`Tag` in `Alltag`), count 0.6 and never recall alone; a
  question compound whose last part alone is in a fact counts 0.6. 0.6.0's suffix test applies only to words the
  dictionary does not know (`Matrose` is no `Rose`, `Ausland` no `Land`). Held-out Wikidata compounds: 94.9 % split,
  99.9 % of splits as Wikidata cuts them. `_DE_COMPOUNDS`.
- **German and Spanish verb forms meet their lemma** (`de_es_forms.tsv.gz`, 251 KB): `ging` / `gehen`, `fue` / `ir` and
  `ser`, `hizo` / `hacer`, at 0.6 of a match and never recalling a fact alone (a form can belong to two verbs, the
  source has errors, a name can look like a form: `Lucía` / `lucir`), in text of that language only and not in a tie
  with English; forms spelled like another word (`Macht`, `vino`, `cuenta`), like a regular form of another verb
  (`fuhren` / `führen`) or listed under more than three verbs (`tuviese`) are left out. The 2,000 German and 4,000 Spanish verbs a stand-in for frequency ranks first. `_LEMMA_FORMS`.
- **Italian, Turkish, Polish, Dutch.** Snowball stems (each word stemmed as written and folded, so a word typed
  without its marks still meets one typed with them), function words, greetings and courtesy pieces, negators,
  "with" / "without" and "but" words. Turkish: `İ`, `I`, `ı` and `i` compare as one (`İstanbul` / `ISTANBUL`, `KIZIM`
  / `kızım`); a case ending after an apostrophe comes off a name (`Ankara'da`, `Ayşe'nin`, `HR24'ün`, `hr-ovh'de`), so
  the name is mentioned and an identifier stays required; the noun without the possessive `s` Snowball leaves
  (`kedisi` / `kedi`). Polish: `ł` compares as `l` and is still searched as written (`szkoła` / `szkole` / `szkola`).
  Italian: elision after `quest'`, `quell'`, `dov'`, `com'`, `cos'`, `anch'` and the like (not before a lone `s`:
  English `dev's`). Dutch: `'s` plurals and possessives (`pinda's`), and a `ge-` that Snowball 3.x takes off is put
  back (`gezin`, family, was `zin`, sense: 6 false joins of 6 on a short list, now 0; `gemaakt` / `maakt` no longer
  meet). Turkish, Polish and Dutch stores use the word scan (consonant changes, `ł` typed as `l`, `raam` / `ramen`);
  Italian does not need it. Dutch compound heads as in German are behind `_NL_COMPOUNDS`, off (2 more right facts, 4
  more wrong ones on a compound set).

### Changed

- **English same-root words: Open English Wordnet.** 0.9.0's suffix rules joined look-alikes the exception list did
  not cover (`Peter` / `pet`, `meters` / `met`, `classic` / `class`) and missed spelling changes. The pairs now come
  from Open English Wordnet 2025 (`english-wordnet-2025.xml.gz`, CC BY 4.0, built on Princeton WordNet 3.1;
  `LICENSES/OEWN-CC-BY-4.0.md`, `LICENSES/OEWN-WNDB-License.txt`): its sense-level `derivation` and `pertainym` links,
  projected to pairs of single words, both as Snowball stems, kept when the shape is a derivation (the shorter stem
  begins the longer one, a three-letter stem only by a common suffix; or the two share all but the last two letters
  of the shorter, and at least three: `decid` / `decis`, `explain` / `explan`, `anger` / `angri`). The filter drops
  learned links that are not one root (`ocular` / `eye`, `costal` / `rib`); a link is kept only when it is the
  derived word's commonest sense (not `butter` / `butt`, `tower` / `tow`, `letter` / `let`, `format` / `form`), and
  when no stem is another, unrelated word (`pearly` stems to `pear`). 5,428 pairs, `en_derivation.tsv.gz` (30 KB); `tools/build_en_derivation.py` rebuilds it. Loaded on the first English word: 0.04 s, 2.4 MB. A root still
  counts 0.6 of a word and never recalls a fact alone. `_EN_DERIVATION = "rules"` restores 0.9.0's rules, `"both"`
  joins the two. Missing from the dictionary: `dentist` / `dental`, `anxiety` / `anxious`; `pronunciation` /
  `pronounce` is in it, but its spelling change is too large for the filter.
- Russian and English dictionaries share one loader (`_related(lang, stem)`); a copy without a data file has no roots
  in that language.
- The bundled language data (`cjk_fold.py` and every `*.gz` table) moved into `dictionaries/`, listed with their
  sources and licences in `dictionaries/__init__.py`.
- Turkish `ı` and Polish `ł` fold to `i` and `l` in every text (only text with those letters changes).
- Latin text of no determined language is stemmed in English, Spanish, French, Portuguese, Indonesian and German as
  before; in Turkish, Polish or Dutch only when it has their letters, in Italian never (Polish stems joined `army` /
  `arm`, `apply` / `apple`).
- A host name stays required in Turkish, Italian, Polish and Dutch text (`hr-ovh'de`): only Spanish, French,
  Portuguese, Indonesian and German text reads a letters-only hyphenated word as a compound, and only when every tied
  language does ("De server hr-ovh" ties Dutch with Spanish).
- A Japanese fact whose spelling the variants changed also keeps it as written, so a question in kanji only (not
  folded: it has no kana) still meets it (`締切` / `締切は明日`). A hiragana variant starts after other hiragana only
  when that is a particle after a kanji or katakana word (`かいちょう` holds no `いちょう`).
- A Chinese one-character word does not recall a fact alone: a pair of the question, or a second of its characters,
  must be there too (`猫几岁了` / `合同里有猫腻`).
- A question told to be Indonesian, German, Turkish, Polish or Dutch scans the store whether or not it holds the probe
  words. Terms of endearment and times of day pad a Turkish courtesy ("Günaydın canlarım!", "Tamam, akşam
  görüşürüz"). A truncated or damaged data file is treated as missing.
- Language detection: function words of the new languages that are everyday words, names or abbreviations elsewhere
  (`come`, `per`, `kim`, `bo`, `door`, `met` …) do not vote for their language and are dropped only where that language
  alone fits; `mia` is not an Italian function word here (a common name).
- snowballstemmer stays `>=2.2,<4`; each stemmer is loaded only if the installed version has it. Polish needs 3.1+
  (current Hermes installs 3.1.1); on 2.x Polish words compare as written and the Polish-stem tests skip.

### Measured

Fresh blind sets, one per language, written by separate agents that never saw the code, run once on the final code
against 0.10.0; every choice was made before that run.

- English (12 same-root questions, 4 other forms, 4 synonyms, 12 look-alike traps): same-root questions found 100 %
  both, recalled 75 → 83 %; all questions recalled 65 → 70 %; traps quiet 12 of 12 both. After the review's filters:
  same-root recalled 75 % (`medical` / `medicine` went with `butter` / `butt`).
- Japanese (14 questions in another spelling, 6 in the same, 4 synonyms, 8 traps): other-spelling questions found
  57 → 71 %, recalled 29 → 43 %; all questions first 46 → 54 %, found 71 → 79 %, recalled 46 → 54 %; traps quiet 8 of
  8 both. After the review's edge fix: other-spelling questions found 64 %, recalled 36 %.
- Chinese (10 questions by a shared word, 4 paraphrases, 4 by a one-character word, 4 synonyms, 10 traps across a
  seam or inside another word): first 59 → 73 %, found 64 → 77 %, recalled 41 % both; one-character words found 0 →
  50 %; traps quiet 6 of 10 both (the traps that still recall share a real word with a fact: `研究` in `研究生` and
  `研究生命`).
- Italian, Turkish, Polish, Dutch (10 other-form questions, 6 same-form, 4 synonyms, 5 small talk, 5 traps each):
  recalled 80 → 85 %, 35 → 85 %, 55 → 75 %, 70 → 85 %; other-form questions recalled 90 → 100 %, 10 → 100 %, 50 →
  90 %, 60 → 90 %; small talk and traps quiet 50 → 90 %, 70 → 50 %, 60 → 70 %, 70 → 60 %. Turkish recalls more where
  it should and where it should not ("Günaydın canlarım!" met a boy named Can; "Tamam, akşam görüşürüz" was not
  read as a sign-off); after the review's fix Turkish small talk and traps are quiet 70 % (not blind).
- German (6 compound questions, 10 other forms, 6 same, 4 synonyms, 10 small talk and traps): first 62 → 69 %, found
  73 → 81 %, recalled 65 → 77 %; compounds 67 → 83 %; other forms recalled 70 → 90 %; quiet 8 → 7 of 10 (`Kopfschuppen`,
  dandruff, is `Kopf` + `Schuppen`, and `Schuppen` is also a shed; after the second review's fixes 8 of 10 again). Spanish: unchanged (73 % recalled both); its
  irregular-verb questions shared another word with their facts, so the set does not test the forms table.
- Every earlier set gives the 0.10.0 numbers in every language, except Chinese (the 0.9.0 sets: first 65 → 80 % and
  75 → 80 %; held-out first 80 → 90 %, third set 88 → 100 %) and one English question ("When is Oliver's dental
  checkup?" still recalled, no longer first). Development sets written with the code (not blind) are in the README.
- 5,000 facts, search and recall together: English 0.02 s and Russian 0.04–0.06 s as before; Chinese 0.16 → 0.21 s
  (first search after start 1.8 → 2.7 s); Turkish 0.01 → 0.25 s (first 1.5 s: Turkish stores now scan, like German).
- An independent review (Codex CLI) of everything above but German and Spanish found nine faults (the "Changed"
  entries from "Latin text of no determined language" on, and the English filters); each has a test in
  `tests/test_holographic_v0110_review.py`. A second review, of German and Spanish and of those fixes, found verb
  forms and three-letter compound heads recalling facts alone, the host-name rule failing in a Dutch tie, and a
  malformed compound row raising; fixed with tests (the German set: same numbers, quiet 8 of 10 again). Known and
  left: a one-word question of no told language against a Turkish, Polish or Dutch store without the probe words, and
  short facts with no function word and none of their language's letters (no stems of the four newer languages).
  612 tests pass without a Hermes core, 600 with snowballstemmer 2.2.0.

## 0.10.0 — 2026-10-13

Russian words of one root come from a dictionary instead of suffix rules.

### Changed

- **Russian same-root words: DerivBase.Ru.** 0.9.0 joined them by stripping suffixes, which cannot tell a derivation
  from a look-alike (`ночные` / `ночь` and `полный` / `пол` look the same to a rule). They now come from DerivBase.Ru
  (Vodolazsky, LREC 2020) as harmonised in Universal Derivations 1.1, Apache-2.0
  (`LICENSES/DerivBase.Ru-Apache-2.0.txt`): its direct parent-child links, as pairs of Snowball stems, kept only when
  the pair has the shape of a derivation — a suffix extension (a three-letter stem only by `-н`, `-ов`, `-ев`, `-ск`,
  `-ин`), or a regular consonant alternation (`друг` / `дружеский`, `г/ж к/ч х/ш д/ж т/ч з/ж с/ш ц/ч`, `ст/ск → щ`).
  The filter also drops the resource's own noise (`пол` / `полк`, `бан` / `банк`, `город` / `гордец`, `дача` /
  `задача`). 46,756 pairs, `ru_derivation.tsv.gz` (269 KB); `tools/build_ru_derivation.py` rebuilds it from the UDer
  file. Loaded on the first Russian word: about 0.3 s and 12 MB. A root still counts 0.6 of a word and still never
  recalls a fact on its own. `_RU_DERIVATION = "rules"` restores 0.9.0's rules, `"both"` joins the two. English keeps
  its one-step suffix rules.

### Measured

A fresh blind Russian set (24 facts, 20 look-alike distractors; 12 questions asked with another word of the fact's
root, 4 with another form, 4 with a synonym, 12 look-alike traps), run once: same-root questions found 75 % (0.8.0) →
83 % (0.9.0 rules) → 92 % (dictionary), recalled 42 → 67 → 67 %; all questions found 80 → 85 → 90 %, first 55 → 60
→ 55 %; traps quiet 11 of 12 in all three. The dictionary misses `дачный` / `дача` (DerivBase.Ru derives дача from
дать). Chosen on the earlier, already read sets (dictionary ≥ rules on each), before this run. Every earlier set as in
0.9.0, except the Russian main set, first 95 → 100 %. 439 tests against a hermes-agent 0.21.6 core.

## 0.9.0 — 2026-10-13

Words of the same root meet in Russian and English; Traditional and Simplified Chinese meet; small talk recalls less;
auto-extraction understands Chinese.

### Added

- **Same-root words.** Snowball removes inflection but keeps derivations apart: "ночные" is `ночн`, "ночь" is `ноч`;
  "teacher" stays `teacher`. A root key strips derivational suffixes off the stem: Russian adjective, verbal-noun, verb
  and diminutive suffixes (`ночные` / `ночь`, `городской` / `город`, `плавание` / `плавает`); in English one step only,
  agent nouns, -ist, -ian, -al, -ic and Porter2's -i (`teacher` / `teaches`, `swimmer` / `swims`, `dentist` /
  `dental`, `photography` / `photograph`; `battery` stops at `batter`, not `bat`). Russian stems of six letters also
  match on their first five (`покупа` / `покупк`). Roots are built only for text whose language is determined —
  English by its function words (a tie that includes English counts), Russian for Cyrillic without Ukrainian or
  Belarusian letters — and a word's roots and beginnings meet only words of the same language, so Spanish `comer`,
  French `le lit` and Ukrainian `головний` join nothing.
- **A root never recalls on its own.** Suffix rules cannot tell a derivation from a look-alike (`войной` / `вой`,
  `полный` / `пол`, `летает` / `лето`), so automatic recall needs at least one word of the message itself in the fact;
  roots then add 0.6 of a word (`_DERIV_WEIGHT`) to its coverage. In `search` a root brings in facts the word's own
  forms missed, through a separate FTS5 query (OR-ed into the main one, a fact holding the word would match the root
  prefix too and score twice), and whenever any candidate meets any term only through a root — its own or the
  fact's — the candidates are ranked by the IDF-weighted share of the query they hold, below facts with the word
  itself. A term with roots is weighed by every fact that may hold it (its forms or its root).
- **Traditional and Simplified Chinese meet.** Traditional characters compare as Simplified ones everywhere text is
  compared (search, recall, duplicate checks, function characters and fillers): `學校` finds `学校`, `醫院` finds `医院`.
  The table (`cjk_fold.py`, 3,222 single characters, the preferred value of each, chains resolved) is derived from
  OpenCC ver.1.4.2 `TSCharacters.txt`, Apache-2.0 (`LICENSES/OpenCC-Apache-2.0.txt`). Stored text keeps its script.
  The n-gram scan moved from SQL `instr` to the facts' cached analysis, so it sees folded text: at 5,000 Chinese facts
  the first search after start takes about 1.5 s, later ones 0.12 s with recall (0.42 s in 0.8.0).
- **Courtesy messages.** A message whose every clause is made of greetings, thanks, sign-offs, reactions and "how are
  you" pieces, at least one of them more than padding, recalls nothing: "Hi, good morning!", "Thanks, that's all for
  now", "Good night, sleep well", "спасибо, на этом всё", "Как твоя неделя?", and the whole-message greetings of every
  language combined ("hola, buenos días"). Pieces start and end at word boundaries ("history" is not "hi" + "story");
  padding alone is a question ("fine?" about a library fine, "сегодня?" about a lesson), and so is a clause with any
  other word ("доброе утро, во сколько балет?").
- **Chinese auto-extraction** (`auto_extract`, off by default), after
  [#2](https://github.com/itpartypattaya/hermes-plugin-holographic/pull/2) by @au61987ai-code — thank you. Preference,
  habit, decision and project-rule sentences in Traditional and Simplified Chinese; `。；！？` end a sentence. A
  preference must open the sentence ("I" or no subject: `你喜歡什麼` and `他喜歡吃辣` are not the user's); `不用` (no
  need), `不會` and passing opinions (`以我來看`) are left out; a project statement needs a deciding subject or the
  project / its naming rules as the subject (`這個文件是我寫的` is not a rule).

### Changed

- **Auto-extraction**, every language: a question is never stored (`?`, `？`, `嗎 / 吗 / 呢` at the end); a dot followed
  by a non-space no longer ends a sentence (`Python 3.12`, `topic.md` were cut in half); a sentence that restates a
  fact the store already holds (write_check's `similar`, checked before anything is written; a possible conflict is a
  change and is stored) is skipped. #2 removed the stored copy instead, which since 0.8.0 would be a forget (erase,
  tombstone, consumers scrubbing) and would block the sentence from ever being extracted again.
- Russian question words (`какой / какая …`, `который …`, `сколько`, `куда`, `откуда`, `почему`, `зачем`) are left out of
  a query's terms, as English `what / which / how / why` are function words: "какая" met "как" in every fact that says
  "считать как …", so "какая сегодня погода" recalled five facts of a live store. They stay in facts: "Иван знает,
  почему" and "Иван знает, куда" are different facts for the duplicate check.
- "the walk" finds "The walker" again, as a word of the same root, below facts with "walk" itself. A bare prefix still
  does not count ("car" does not find "cart"); one word without context ("walk") is of no determined language and gets
  no roots.
- `write_check` collects its candidates with exact words only (`search(..., roots=False)`, scan included), so a word of
  the same root cannot crowd a conflict out of them; duplicate and conflict overlaps never use roots.

### Measured

Blind sets by agents that never saw the code (README, Languages). Same-root words, second Russian and English set:
Russian first 45 → 55 %, found 55 → 60 %, recalled 55 → 55 %; English first 60 → 60 %, found 70 → 75 %, recalled
60 → 60 %. Small talk, a fresh set (30 small-talk messages and 15 questions per language): quiet English 50 → 67 %,
Russian 63 → 70 %; questions found 100 % before and after. Every earlier set of the 14 languages gives the 0.8.0
numbers, except the Russian unseen set's one miss ("во сколько ночные бэкапы"), now found (not recalled: the fact
shares only a root with it), and one more extra fact recalled in one language of the Japanese / Korean / Thai /
German set. Chinese, a fresh set with facts in both scripts: questions in the other script found and recalled
33 → 100 %; all questions first 55 → 75 %, recalled 50 → 70 %; small talk quiet 80 % both. 432 tests against a
hermes-agent 0.21.6 core. Two independent reviews (Codex CLI): the first found look-alike joins, language leaks, a
ranking and a weighting fault and a `write_check` side effect; the second, padding-only courtesy, question words lost
from duplicate checks and a capital-letter name rule that lost ordinary words ("Baby" / "babies") — removed, it gained
nothing measurable. Each is fixed and its examples are tests.

## 0.8.0 — 2026-10-12

The store keeps the history of every fact, records where each version came from, and makes "forget" final.
Contract: [docs/history.md](docs/history.md).

### Added

- **History.** `fact_versions` keeps every version of a fact with its valid time (`valid_from` / `valid_to`, dates,
  half-open) and recorded time; `facts` stays the current projection, so ordinary search, prefetch, entities and HRR
  never see a past version. New actions say how a fact stopped being current: `change` (it changed; same fact id),
  `end` (no longer holds, nothing replaces it), `correct` (it was wrong; never returned as the past), `revert` (undo
  the last change, correction or end), `forget`; `history {fact_id}` lists the versions.
- **`as_of=YYYY-MM-DD`** on `search` and `list`: what held on that date, ranked over the versions of that date only
  (so `limit=1` is the best version for the date). Answers separate `results` from `undetermined`: a fact without a
  known start is known to hold only from the day it was recorded, and an undated change gives the old version an
  upper-bound end; the store does not invent history. `probe`, `related` and `reason` refuse `as_of`.
- **Source of each version:** via (`tool` / `auto_extract` / `mirror`; `legacy` for facts from before 0.8.0),
  platform, chat type, chat / thread / session id, author of the turn. Shown by `list`, `history`, `export`; in the
  facts recalled before a turn only with **`recall_source: short`** (default `off`). Chat names are not stored.
- **A fact that comes back** (X → Y → X): `add X` answers `past_version` with the fact id instead of storing a
  duplicate; the write check lists near `past_versions`; auto-extraction skips sentences that are past versions.
- **Tombstones** (`forgotten`): the id and keyed hashes of a forgotten fact's texts, never the text. Auto-extraction
  does not store a forgotten sentence again, nor does an `add` from a cron job (`status: "forgotten"`); a manual
  `add` by the main agent is stored with a hint; other tools read the ids to clean their copies.
- `stats` reports past versions, corrected versions, ended facts and tombstones.

### Changed

- **`forget` deletes for good**, and `remove` is now `forget`: the fact, every version, links, orphaned entities and
  aliases, banks; both FTS indexes are rebuilt (an FTS5 delete leaves the words in old segments), the connection runs
  with `secure_delete`, the file is rewritten with `VACUUM` and the WAL truncated. If another process blocks the
  rewrite, the result says `scrub: "pending"` and the next open or forget finishes it.
- **`add supersedes=X` is `change X`: the id stays `X`** (before, the new text got a new id and the old fact was
  deleted). The answer still carries `superseded`. If the new text is already another fact, `X` ends and that fact is
  returned.
- `update content=` is a correction: the old text is kept as a `corrected` version. Tags, keywords, category and trust
  still change in place.
- A `change` drops the old text's keywords (they described the old text) unless new ones are given, and starts the
  trust over, as `supersedes` did.
- Built-in memory mirroring (`mirror_memory_writes`): `replace` is a `change`, `remove` is a `forget`.

### Fixed

- **A cron job could not write facts.** Since 0.2.0 the plugin refused every write in non-primary contexts, cron
  included, while the copy bundled in Hermes core never did: memory-maintenance jobs that store facts by calling
  `fact_store` (hermes-dreaming's nightly extraction, its keyword pass) stopped working after the move to this plugin.
  New **`cron_writes`** (default `true`): a cron job's explicit `fact_store` / `fact_feedback` calls write;
  auto-extraction, mirroring and recall counts stay off in cron, and subagents and flush contexts stay read-only.

### Compatibility

- `facts` gets no new column. A file an older release wrote to is reconciled on the next writer open (new facts get
  a legacy version; a text changed by an older `update` closes the old version with an upper-bound end; a fact an
  older `remove` deleted is forgotten). Read-only contexts do not migrate; `as_of` / `history` there say so. CI runs
  0.8.0 → v0.1.1 → 0.8.0 and 0.8.0 → v0.7.0 → 0.8.0 on one database.

## 0.7.0 — 2026-10-12

### Added

- **`scope: chat`** — a separate database per chat (`holographic-chats/<platform>-<chat id>.db` next to `db_path`),
  for one bot serving many unrelated groups: the provider recalls facts into every turn on its own, and without
  isolation one group would hear what was said in another. **`scope: thread`** — per forum topic / thread
  (`…-t<thread id>.db`), the chat's own database outside threads. A file per chat, not a `chat_id` column: facts meet
  in the FTS index, the n-gram scan, entities, HRR banks and the write checks, and one missed filter would leak between
  chats. The core already passes `chat_id` / `thread_id` to memory providers; no core change. Without a chat (CLI,
  cron, subagents) nothing is recalled or written, as in `scope: user`. Existing facts stay in the profile database;
  tools that read `memory_store.db` directly (e.g. hermes-dreaming) do not see per-chat databases. Per-turn
  auto-extraction (`auto_extract`) files facts into the active store — the chat's, never the profile one.
  ([#1](https://github.com/itpartypattaya/hermes-plugin-holographic/issues/1))

## 0.6.2 — 2026-10-12

### Added

- **`recall_indicator`** (default `true`). The core turns `recall_status` into a status line
  ("🧠 Holographic — recalled 1 memory"); on Telegram and other messaging platforms it arrives as a separate message
  before every reply that used memory, in English, in family and group chats alike — the bundled v0.1.1 never reported
  it, so installs moving to this plugin see it for the first time. `recall_indicator: false` under
  `plugins.hermes-memory-store` turns the line off; what is recalled does not change.

## 0.6.1 — 2026-10-11

### Fixed

- **Keywords no longer make a fact look recently changed.** `update` with keywords alone stamped `updated_at` like
  any edit, so every fact given keywords afterwards (by `enrich`, by hand or by a nightly job) looked fresh: temporal
  decay favoured it, and a job that reviews recent facts (hermes-dreaming) would have taken a backlog of old facts
  for new ones. Keywords are a search aid, not a change of the fact: an `update` that carries only `keywords` keeps
  `updated_at`; one that also changes content, tags, category, trust or entities stamps it as before.

## 0.6.0 — 2026-10-11

Synonyms and words of another root through keywords the agent gives when it stores a fact, and four more languages
measured: Japanese, Korean, Thai, German (README, "Languages").

### Added

- **`keywords`** on `fact_store` add / update: 3–8 words someone might ask with that the fact does not contain
  (synonyms, other roots, broader terms, translations). Kept in the indexed tags column after a `keywords:` line (no
  schema change; older versions index them too), searched like the text by FTS5, n-grams and the word scan, never
  shown in the recalled block, validated like a fact. `update` replaces them and keeps the user's tags. **`enrich`**
  lists facts without keywords; `stats` counts them. The tool description tells the agent to search again with a
  synonym or a translation when nothing fits. Measured on 112 questions in 14 languages that share no word with their
  fact: found 42 → 94, recalled 33 → 88, with keywords from an agent that never saw the questions.
- **German**: Snowball stems, function words, greetings, negators; a word matches the end of a compound in German text
  (`Katze` / `Familienkatze`), and German stores use the word scan for that.
- **Japanese, Korean, Thai**: Japanese particles weigh like Chinese function characters; Thai trigrams are weighted by
  Thai function words; whole-message fillers (はい, 네, โอเค) and greetings in the three languages recall nothing.
  Thai chit-chat recalled facts 4 times in 5, Korean and German once; now none.

### Changed

- Only Chinese characters (also in Japanese text) count one by one in the relevance gate; kana, Hangul and Thai
  letters are sounds, and Thai matched chit-chat through them.
- Thai claims compare by trigrams (they compared single letters, so unrelated facts looked like conflicts).
- A negation is a possible conflict when the shorter claim is at least 60 % contained in the other (was a 0.5 word
  overlap): "eats meat every day" / "does not eat meat" counts, "likes cats" / "does not like cooking" does not.

## 0.5.0 — 2026-10-11

Seven more languages: Spanish, Hindi, Arabic, French, Bengali, Portuguese, Indonesian (the most spoken after English,
Chinese and Russian). Measured on the same kind of stores as 0.4.2 (README, "Languages"): before, Arabic recalled the
right fact before the turn for 50 % of questions, French 75 %, Spanish 85 %, and chit-chat recalled facts in four
languages; now 100 % on the main sets, and the right fact is found and recalled for every question of the unseen sets.
Reviewed before release by Codex CLI; its findings are fixed below, each with a test.

### Added

- **Stems per language**: Snowball for Spanish, French, Portuguese, Indonesian and Hindi (Latin words were all stemmed
  as English), with the Portuguese `-ões` and Spanish `-ces` plurals Snowball keeps apart; Arabic light10 with
  Snowball's Arabic and the word without a glued conjunction/preposition before an alef; a light Bengali suffix
  stripper that leaves at least three letters. The language of Latin text is told by its function words; text with
  none gets all five Latin stems.
- **Function words per language** (ten lists): a Spanish or Hindi "thanks" recalls nothing. A word is dropped only as a
  function word of the language the other words show (or, in a sentence of four words or more, it alone shows); with
  no other evidence the English list applies, so "son" or "bisa" alone stays a search word.
- **Arabic, Indic scripts and Indonesian are also matched word by word** against the facts' text (Latin words of a
  question of undetermined language too, if the store holds Indonesian facts): the FTS5 index splits Devanagari and
  Bengali words into letters and cannot see past Arabic (`ال`, `ل`, `بال`) or Indonesian (`me-`, `di-`) prefixes. FTS
  still ranks what it finds; the scan fills in. Rows are read on every scan, a fact is analysed again only when its
  text changed: 0.03–0.07 s per search at 5,000 facts, after a first scan that analyses every fact (about 1 s at
  5,000, 4–6 s at 20,000). A store without such facts never scans.
- **Folding**: Latin accents and ligatures, Arabic vowel marks and letter variants (`أ` = `ا`, `ة` = `ه`), the Indic
  nukta, native digits (searched in each digit system too, inside identifiers as well: `2027` finds `২০২৭`, `HR24`
  finds `HR২৪`). Control characters never reach an FTS5 expression.
- **Tokens**: Unicode punctuation stripped (`¿`, `؟`, `।`); French elision (`d'Eva`) and, in French text, inversion
  (`prend-il`, `est-ce`, but not `rendez-vous`); Portuguese enclisis in Portuguese text; reduplication (`anak-anak`); a
  hyphen between scripts splits (`hr-ovh-এর`). Letters-only hyphenated tokens stay identifiers in English text and
  text of no determined language (`hr-ovh`, also inside Chinese text); in Spanish, French, Portuguese and Indonesian
  text they are compound words.
- **Greetings** in the seven languages; negators (`no`, `pas`, `não`, `tidak`, `لا`, `नहीं`, `না`).

### Changed

- Latin-script entity names are matched as written (or with `'s`): `Jean` is not mentioned by `jeans`, `Rose` not by
  `roses`. Names in other scripts still match their inflections.
- A long Latin stem reaches the words that extend it (`allergi` → `allergic`, as the FTS prefix does); the looser
  "shared beginning" rule of 0.4.2 now applies to Russian only, so `contract` no longer matches `contrast`.
- Possible conflicts compare claims by word keys without function words, keeping "with" / "without" and their
  equivalents; a negation after "but" belongs to another clause; a conflict is reported before a near-duplicate ("the
  budget is 500" / "600" is an update). "Ana come carne todos los días" / "Ana no come carne" is flagged.
- `reason()` applies every constraint before its cap of 500 and keeps the most trusted; scan candidates are cut by
  coverage, then trust, then age, and re-checked against the row as read.
- Facts of a named entity come back newest first when their timestamps tie (were in arbitrary order; the second-set
  numbers published with 0.4.2 were partly that luck and are corrected in the README).

## 0.4.2 — 2026-10-11

Language quality, measured on English, Russian and Chinese stores (see README, "Languages").

### Fixed

- **Chinese**: function characters (可以, 什么, 怎么, 我们) no longer count as topic; a chit-chat line such as
  "可以，继续吧" recalled a fact that also said 可以. Recall before the turn also accepts IDF-weighted coverage of the
  meaningful characters (stricter, 0.5), since a one-character word (猫) or a reordered phrase (名字 / 名叫) shares
  characters rather than bigrams. Main-set recall 75 % → 100 %.
- **Latin words inside Chinese text** ("猎头公司HR24", "服务器hr-ovh运行") are found: the tokenizer glues them to the
  characters around them, so they are matched as substrings, and an ASCII identifier's word boundary is ASCII-only.
- **Declared Chinese names** are recognised inside a message (no spaces around them), so their facts join the recall.
- **reason** with a name that is not an entity matches it in any word form ("allergy" finds "allergic"; was an exact
  phrase), and Chinese words by substring.
- **Conflicts in Chinese**: claims compare by characters with 不/没/无 as negation ("安东喝黑咖啡，不加糖" vs
  "安东喝咖啡加牛奶和糖" is flagged).
- **Russian derivations** Snowball keeps apart ("переезжаем" / "переезд") meet on their shared beginning (stems of
  7+ letters, first len-2 letters, at least 5).
- **Greetings** ("good morning", "доброе утро", "早上好") are courtesies: they no longer recall facts about mornings.
  "чём" and other ё-forms are stopwords.

## 0.4.1 — 2026-10-11

Fixes from a second independent review (Codex CLI, whole branch 0.1.1..0.4.0), each with a regression test.

### Fixed

- **User scope failed open**: an anonymous turn in a shared chat kept the previous person's store active, and a bot
  without an id passed the bot check. Now a bot, or an anonymous turn once the conversation has had authors, is
  nobody (no recall, no writes); a conversation that never had authors is the user Hermes named at start.
- **Extraction mixed people**: in user scope, session-end and pre-compression extraction stored every participant's
  sentences in the active person's store. Extraction now runs per turn in `sync_turn`, filed under `turn_author`;
  unattributable turns and whole transcripts are skipped.
- **Thread safety**: a scope switch could happen in the middle of a recall or tool call (the gateway calls hooks from
  several threads, and core leaves a timed-out prefetch running). Provider entry points are serialized.
- **Buffered recall counts** could be written to another user's or profile's database (fact ids are per database).
  Buffers are kept per database, and dropped rather than transferred if it stays busy until shutdown.
- **Migration lost legacy aliases** when it merged two entities with the same key; they are imported first now, and
  every merge keeps the merged entity's name as an alias of the survivor.
- **Aliases of entities an older version deleted** still resolved to the dead id, and new facts were linked to it.
  They are dropped on open, and alias lookups only follow live entities.
- **Alias collisions**: giving an alias that already belongs to another entity merged that whole entity and lost its
  name; it is refused now with a hint to merge explicitly.
- **Entity cache after a rollback** could link a fact to the wrong entity (a rolled-back id reused). The cache now
  follows a generation counter (bumped on every entity change and rollback) and SQLite's `data_version`.
- **Merges** keep the declared flag, re-encode the vectors of the facts involved, drop the category banks and copy
  the moved aliases into the legacy column older versions read.
- **Vectors of another size** written by an older version or setting are re-encoded on open (checked from the blobs,
  not only from the recorded dimension). Subagent, cron and flush contexts never migrate or repair a store.
- **Search**: no user query can produce an FTS5 syntax error (every MATCH token is operator-free, with or without the
  stemmer); a stem prefix match is kept only if the fact really contains the word or its stem ("walk" no longer
  returns "walker").
- **Recall gate**: words of the message that no fact contains now count against coverage ("do submarines deploy on
  Jupiter?" is not about "we deploy software"); a message naming an identifier recalls only facts with that exact
  identifier ("pve-01" is not "pve-010").
- **reason** with a known and an unknown name intersects the known entity's facts (aliases included) with the unknown
  name's words.
- **update** with only `entities` links them; rewording a fact keeps the entities declared for it.
- **Conflicts** see negation ("likes apples" / "does not like apples"); such a pair is never reported as a duplicate.
- **No-repeat window** keys facts by content revision (two edits in the same second are different), drops recent
  facts before the top five are taken, and resets on context compression.
- **Validator** catches requests to send credentials somewhere and ignores phrases inside quotes.
- `backup_paths()` lists per-user stores next to an external `db_path`; at most 16 per-user stores stay open.
- Bounded work: `write_check` looks at 50 facts per entity at most, `review` and `contradict` load only what they
  compare, entity mentions are looked up by stem instead of scanning every name.

### Known limits

- A timed-out recall is discarded by core, but the provider cannot tell: its facts count as recalled and the recall
  indicator may show them.
- Word forms are stems used as prefixes, not lemmas: an irregular form may miss (`studies` vs `study`).

## 0.4.0 — 2026-10-11

Schema version 2, additive: older versions (0.1.1, the bundled core copy) keep working on the same file, and what
they write is reconciled on the next open. CI: 0.4.0 → 0.1.1 → 0.4.0 on one database.

### Added

- **Entity identity**: `entities.name_key` (NFKC, casefold, hyphen/underscore as space) with a unique index, so
  `Hermes Agent`, `hermes-agent` and `HERMES AGENT` are one entity. Entities an older version created without a key
  are keyed on open and merged into an existing one with the same key.
- **Aliases**: `entity_aliases` table; `X aka Y` makes `Y` an alias of `X` (it used to create two unrelated
  entities); actions `entities` (names, aliases, fact counts) and `alias` (second name, or merge two entities).
  Aliases are mirrored into the legacy `entities.aliases` column that older versions read.
- **HRR dimension recorded** in `meta`: changing `hrr_dim` re-encodes the stored vectors on open instead of making
  every vector query fail; the dimension of pre-0.4.0 stores is inferred from their blobs.
- **Thai, Chinese, Japanese, Korean**: matched by character n-grams (trigrams for Thai/Lao/Khmer/Myanmar, bigrams for
  CJK and Hangul) in search and in the recall gate. A substring scan, no extra index or migration.
- **Per-user stores** (`scope: user`): one database per `<platform>-<user id>` under `holographic-users/`, chosen by
  the user Hermes passes at start and switched to the author of each turn in shared chats (`on_turn_start`). No
  identity, or a bot author: fails closed (no recall, no writes). Separate files instead of a scope column: the
  global `UNIQUE(content)` keeps working per user, and an older version cannot expose one user's facts to another
  because it never opens those files.

### Changed

- contradict compares only facts that share an entity (grouped), not every pair.

## 0.3.0 — 2026-10-11

Recall and writing quality, from user reports (r/hermesagent: "can't tell if it works", "the agent never stores
anything", "fills up with junk", "an old IP stayed next to the new one") and the audit. No schema change: one more
use of the existing `entities.entity_type` column, and 0.3.0 → 0.1.1 → 0.3.0 still passes on one database.

### Added

- **Word forms**: search matches the exact word or its Snowball stem as an FTS5 prefix (English for Latin script,
  Russian for Cyrillic): `Маши`/`Машу` find `Маша`, `байк` finds `байке`, `deploys` finds `deploy`. Identifiers
  (`pve-01`, `v0.21.6`) are never stemmed. New dependency `snowballstemmer` (pure Python, BSD).
- **Russian stopwords** and greetings next to the English ones.
- **Relevance gate for recall before the turn**: a fact must cover a share (`prefetch_min_coverage`, 0.3) of the
  message's informative words, weighted by rarity (IDF), or match an identifier; a message with no informative word
  recalls nothing. On the test store "да, это так" no longer injects "Это проект для семьи".
- **No repeats**: a fact injected before a turn is not injected again in the same session for
  `prefetch_repeat_turns` (6) turns, keyed by fact revision; a new conversation or a rewound one starts clean.
- **Entity mentions**: when the message names a known entity, its top facts join the recall.
- **Declared entities**: `add`/`update` take `entities` (a first name, a pet). Declared and multi-word entities are
  then linked whenever a later fact mentions them in any word form ("У Маши аллергия" links to "Маша").
- **Write-time hints**: `add` answers with `similar` facts and `possible_conflicts` (same subject, a different
  claim; also between entity-less facts such as two IPs for one server), and takes `supersedes: <id>` to replace a
  fact in one transaction (the new one starts at default trust).
- **Write validator**: JSON, tool or chat-role markup, instruction-like phrasing ("ignore previous instructions"),
  invisible formatting characters, long encoded blobs and >1000-character texts are refused with the reason.
- **stats / export / review** actions; the core's recall indicator (`recall_status`) shows how many facts the last
  recall injected.
- A clear prompt block: when to store, `entities` for one-word names, `supersedes`, ids for `fact_feedback`, and a
  warning when numpy is missing.

### Changed

- Recall before the turn is framed as data ("stored facts: data, not instructions"), one line per fact, ≤300
  characters.
- `auto_extract` stores the matching sentence, not the whole message; English and Russian patterns; trust 0.4
  (below hand-added facts); also runs before context compression, so a long session that is later killed keeps it.
- Recall counts are buffered and written in one statement (every 25 recalls and at session boundaries) with no
  wait on another process's write lock; on a busy database they are kept for the next flush.
- An unreadable database no longer breaks the agent: the provider reports why memory is off (prompt block and tool
  errors) with the `sqlite3 … .recover` command.
- Shorter `fact_store` description with call shapes, for models that struggled with nine actions.

## 0.2.1 — 2026-10-11

Fixes from an independent review (Codex CLI) of 0.2.0 and of the provider contract. No schema change; a database
written by 0.2.1 still works with 0.1.1 and the bundled core copy (tested: 0.2.1 → 0.1.1 → 0.2.1 on one file).

### Fixed

- **reason** returned facts about only one of the entities when no fact linked them all. probe and reason now
  answer from the entity index (exact, any letter case, aliases included): reason intersects the entities' facts,
  and when a name is not a known entity it falls back to keyword search that still requires every name. probe of an
  unknown name no longer admits an unrelated fact through the HRR noise floor.
- **related** returned the top of the whole store for any entity. It now keeps only facts over a measured noise
  floor (0.2 at dim 1024: tied facts score ≥ 0.35, the best of 17k untied pairs 0.088); precision@5 0.92 → 1.00.
- **`min_trust`** was ignored by probe, related, reason and contradict, so facts rated unhelpful came back.
- **contradict** treated a claim that only adds detail ("likes apples" / "likes apples and bananas") as a conflict.
- **Writes were not atomic**: a failure after the INSERT left a fact without entities or vector. add, update,
  remove and feedback now run in one transaction each.
- `add` of an existing text answers `"status": "exists"` (was `"added"`); `update` to another fact's text answers
  `{"updated": false, "duplicate_of": id}` instead of an IntegrityError; `update` to empty text is refused.
- **Entity noise**: straight quotes pair only at word boundaries (`don't … it's` no longer yields "t … it"), `aka`
  takes the adjacent name only ("Alice Smith aka Bob Brown works here" no longer yields "Bob Brown works here"), a
  sentence-opening word is dropped ("So Apple"), and flags, `key=value`, paths, URLs and IPs are rejected. Names are
  matched with Unicode casefolding (SQLite `LIKE` folds ASCII only and treated `_`/`%` as wildcards). Entities left
  without facts are removed.
- **Search**: `pve-01` and `host:22` are searched as the phrases `pve 01` / `host 22` (the tokens the index holds;
  they used to collapse to `pve01`). A query with nothing searchable no longer sends the raw text to FTS5.
- `record_retrievals` counts integer ids only (`1.9` counted fact 1).
- A damaged or foreign-dimension HRR vector is skipped and logged instead of failing the query.
- Counter, trust and vector updates no longer re-index the FTS row (the update trigger now fires on content and
  tags only; older databases are migrated on open, and older versions keep working with it).
- Category banks were rebuilt from every vector of the category on each write (and logged an SNR warning per write
  past 256 facts) though nothing reads them since 0.2.0; a write now just drops its category's bank. A downgraded
  version finds no bank and uses the per-fact vectors.
- Reads take the store lock.

### Changed (provider contract)

- Subagent, cron and flush contexts (`agent_context`) read but never write: no facts, feedback, counters,
  extraction or mirroring.
- `initialize` opens the database of the `hermes_home` it is given and releases a previous handle when called again;
  `save_config` writes the profile it is given.
- `on_memory_write` takes the `metadata` argument; with `mirror_memory_writes` on, `replace` and `remove` follow the
  entry by `metadata["previous_content"]`.
- Re-running `hermes memory setup` offers the saved values as defaults instead of the factory ones (it used to switch
  `db_path` to a fresh file and turn `auto_extract` off).
- `backup_paths()` lists a database outside the Hermes home; the database and its WAL files are owner-only on POSIX.
- `limit` is clamped to 1–100; `fact_feedback` refuses actions other than helpful / unhelpful.

## 0.2.0 — 2026-10-10

First release under new maintenance (itpartypattaya). Measured on a synthetic store of 200 facts about 20 people
with random trust scores; before → after.

### Fixed

- **probe** returned the most trusted facts instead of the entity's facts (precision@5 0.02–0.04 → 1.00; a random
  pick scores ~0.06). It compared `unbind(fact, bind(entity, ROLE_ENTITY))` with the fact's content binding, but a
  fact vector is a *bundle* of bindings, so unbinding one leaves noise, not the content. probe now measures the
  fact's similarity to `bind(entity, ROLE_ENTITY)` directly and drops facts under a noise floor (4 sd of chance
  similarity), so trust only orders real matches. The category-bank shortcut had the same flaw and is gone.
- **reason** (facts linking several entities) found the right fact in the top 5 in 1 of 15 cases → 15 of 15, same fix.
- **contradict** flagged facts that share a subject and are *dissimilar*, i.e. different topics ("Carol King works
  at the bakery" / "Carol King plays guitar"), and missed real conflicts. It now pairs facts that share entities and
  make a similar, non-identical claim, by word overlap with the entity names left out ("lives in Paris" / "lives in
  London"). `content_similarity` in its output is now that word overlap. It no longer needs numpy.
- **search**: the HRR term compared the query with the whole fact vector instead of its content binding, so it was
  ~0.5 for every fact (a constant). It now compares `bind(query, ROLE_CONTENT)`: ~0.69 for facts on the query's
  topic vs ~0.51 for others.
- **`retrieval_count`** was declared and never written. It now counts facts handed to the model: prefetch and the
  search / probe / related / reason actions (not list or contradict). `updated_at` is not touched. Same fix as the
  unmerged hermes-agent#130930.
- **Entity names** were ASCII-only (`[A-Z][a-z]+`). Capitalized multi-word names in any cased script are linked now
  (`Мария Иванова`, `Jean-Luc Picard`), guillemets and curly quotes count as quotes, and a quoted span of more than
  three words is treated as a quotation, not a name. Related: unmerged hermes-agent#129521 and #116232.
- **FTS5 failures** were swallowed silently (an empty result looked like "no memories"); they are logged as warnings.
  Related: unmerged hermes-agent#127134.

### Changed

- **Built-in memory is no longer mirrored into the fact store by default.** Every `memory add` used to be copied as a
  fact; MEMORY.md / USER.md are already in the system prompt in full, so prefetch added the same sentence twice and
  the copy outlived the entry when it was reworded or removed. Set `mirror_memory_writes: true` for the old behaviour.
  Facts already mirrored stay in the store.
- Prefetched facts carry their id (`- #12 [0.5] …`) so the model can call `fact_feedback` on them. Related: unmerged
  hermes-agent#121513.
- The `fact_store` description no longer tells the model to "ALWAYS probe or reason first": it says to search first,
  probe a named entity, reason across several.

## 0.1.1 — 2026-10-02

Nous Research: standalone copy of the provider bundled in hermes-agent, synced with core, tests ported.
