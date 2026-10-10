"""Text normalisation shared by search, the prefetch gate, entity matching and duplicate checks.

Every word gets comparison keys: its folded form plus its stem in the language of the text, and two words match when
their keys meet, so "Маши" finds "Маша", "kucingnya" finds "kucing" and "لإيفا" finds "إيفا". The language comes from
the script (Cyrillic: Russian; Arabic script: Arabic; Devanagari: Hindi; Bengali script: Bengali) and, for Latin
script, from the function words the text uses ("the", "el", "le", "o", "yang": English, Spanish, French, Portuguese,
Indonesian); Latin text with none of them gets the stems of all five. Stems come from Snowball (English, Russian,
Spanish, French, Portuguese, Indonesian, Hindi), Larkey's light10 for Arabic (as in Lucene) next to Snowball's, and a
light suffix stripper for Bengali. Folding drops Latin accents, Arabic vowel marks and letter variants and the Indic
nukta, and turns native digits into ASCII. A stem is a search key, not a dictionary lemma. Identifiers (anything with
a digit, or a dotted / pathed token) are never stemmed and never stopwords. Chinese, Japanese, Korean and the scripts
of Southeast Asia are compared by character n-grams instead (see UNSEGMENTED)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cached_property, lru_cache

try:  # pure Python, BSD; optional: without it words compare exactly (Arabic and Bengali keep their light stemmers)
    import snowballstemmer
    _STEMMERS = {code: snowballstemmer.stemmer(name) for code, name in (
        ("en", "english"), ("ru", "russian"), ("es", "spanish"), ("fr", "french"), ("pt", "portuguese"),
        ("id", "indonesian"), ("hi", "hindi"), ("ar", "arabic"))}
except ImportError:  # pragma: no cover - exercised only without the dependency
    _STEMMERS = {}

PUNCT = ".,;:!?\"'()[]{}#@<>«»“”„…"
_MIN_STEM = 3  # shorter stems match too much as prefixes ("ма*")
_LOOSE_FROM, _LOOSE_MIN = 7, 5  # stems this long also match on their first len-2 (at least 5) letters

# -- scripts and folding -----------------------------------------------------------------------------------------

def _script(ch: str) -> str:
    o = ord(ch)
    if o < 0x250 or 0x1E00 <= o <= 0x1EFF:
        return "latin"
    if 0x400 <= o <= 0x52F:
        return "cyrillic"
    if 0x600 <= o <= 0x6FF or 0x750 <= o <= 0x77F or 0x8A0 <= o <= 0x8FF or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
        return "arabic"
    if 0x900 <= o <= 0x97F:
        return "devanagari"
    if 0x980 <= o <= 0x9FF:
        return "bengali"
    if 0xA00 <= o <= 0xDFF:
        return "indic"
    return "other"


def word_script(word: str) -> str:
    """Script of the first letter; "common" for a token without letters (numbers, symbols)."""
    return next((_script(c) for c in word if c.isalpha()), "common")


# Scripts the FTS5 unicode61 index cannot serve: it splits Indic words at their vowel signs (marks are not token
# characters), and Arabic attaches conjunctions, prepositions and the article to the front of a word, where a prefix
# query cannot see past them. Words in these scripts are matched against the text of every fact instead.
_SCAN_SCRIPTS = frozenset({"arabic", "devanagari", "bengali", "indic"})
_SCRIPT_LANGS = {"cyrillic": ("ru",), "arabic": ("ar",), "devanagari": ("hi",), "bengali": ("bn",)}
LATIN = ("en", "es", "fr", "pt", "id")

_FOLD = str.maketrans({
    **{c: "ا" for c in "أإآٱ"}, "ى": "ي", "ة": "ه", "ـ": None,  # Arabic letter variants, tatweel
    **{chr(c): None for c in range(0x64B, 0x660)}, "ٰ": None,  # Arabic vowel marks
    "़": None, "ँ": "ं",  # Devanagari nukta; candrabindu written as anusvara
    "়": None, "ঁ": None,  # Bengali nukta and candrabindu
    **{chr(base + d): str(d) for base in (0x660, 0x6F0, 0x966, 0x9E6) for d in range(10)},  # native digits
})


def _fold_text(text: str) -> str:
    if text.isascii():
        return text
    out, latin = [], False
    for c in unicodedata.normalize("NFD", text):
        if "̀" <= c <= "ͯ":
            if latin:
                continue  # an accent on a Latin letter; the breve of Cyrillic й stays
        else:
            latin = c.isascii() or _script(c) == "latin"
        out.append(c)
    return unicodedata.normalize("NFC", "".join(out)).translate(_FOLD)


@lru_cache(maxsize=65536)
def fold(word: str) -> str:
    """Comparison form of a lower-cased word: Latin accents dropped ("está" = "esta"), Arabic vowel marks, tatweel and
    letter variants normalised (أ إ آ -> ا, ى -> ي, ة -> ه), the Indic nukta dropped, native digits as ASCII."""
    return _fold_text(word)


def _folded(words: str) -> frozenset:
    return frozenset(fold(w) for w in words.split())


# -- function words ------------------------------------------------------------------------------------------------

STOP = {
"en": _folded("""
    a about above after again all am an and any are as at be because been before being between both but by can could
    did do does doing don down during each few for from further had has have having he her here hers herself him himself
    his how i if in into is it its itself just me more most my myself no nor not now of off on once only or other our
    ours ourselves out over own same she should so some such than that the their theirs them themselves then there these
    they this those through to too under until up very was we were what when where which while who whom why will with
    would you your yours yourself yourselves ok okay yes yeah thanks thank please hi hello hey"""),
"ru": _folded("""
    а без более бы был была были было быть в вам вас весь во вот все всего всех вы где да даже для до его ее её если
    есть еще ещё же за здесь и из или им их к как ко когда кто ли либо мне может мы на над надо наш не него нее неё нет
    ни них но ну о об однако он она они оно от очень по под при про с со так также такой там те тебе тем то того тоже
    той только том ты у уже хотя чего чей чем что чтобы чье чья эта эти это этот я ага ок окей спасибо привет пожалуйста
    да нет ладно хорошо понял поняла давай конечно просто вообще кстати понятно ясно угу кого кому чему чем ведь вон
    чём всё моё твоё своё нём"""),
"es": _folded("""
    a al algo alguna algunas alguno algunos algún ante antes como cómo con contra cual cuál cuales cuando cuándo cuanto
    cuánto cuanta cuánta cuantos cuántos cuantas cuántas de del desde donde dónde adónde durante e el él ella ellas ellos
    en entre era eran eres es esa esas ese eso esos esta está estaba estaban estamos están estar estas estás este esto
    estos estoy fue fueron fui ha había han has hasta hay he hemos la las le les lo los más me mi mí mis mucho muchos
    muy nada ni no nos nosotros nuestra nuestro o os otra otras otro otros para pero poco por porque que qué quien
    quién quienes quiénes se sea ser si sí sin sobre sois somos son soy su sus también tanto te tenemos tener tengo
    tiene tienen ti tu tú tus un una unas uno unos usted ustedes vosotros y ya yo cada hacer hace hacen hago puedo
    puede pueden puedes podemos debo debe hola gracias vale ok bueno claro dale vamos"""),
"fr": _folded("""
    à a ai as au aux avec avez avoir avons c ça ce ceci cela celle celles celui ces cet cette chez comme comment d dans
    de des donc dont du elle elles en encore est et étaient était été être eu eux fait faire fais font il ils j je l la
    le les leur leurs lui m ma mais me même mes moi mon n ne ni nos notre nous on ont ou où par pas peu peut peux
    pouvez pour pourquoi qu quand que quel quelle quelles quels qui quoi s sa sans se ses si son sont sous suis sur t
    ta te tes toi ton tous tout toute toutes très tu un une vers vos votre vous y combien quelque lequel laquelle oui
    non merci salut bonjour accord ok va vais vas allons allez vont aller faut"""),
"pt": _folded("""
    a à ao aos as às o os um uma uns umas de do da dos das em no na nos nas num numa por pelo pela pelos pelas para pra
    com sem e ou mas que se não mais como quando onde quem qual quais quanto quanta quantos quantas porque porquê eu tu
    você vocês ele ela eles elas nós me te lhe lhes meu minha meus minhas teu tua seu sua seus suas nosso nossa este
    esta estes estas esse essa esses essas isto isso aquilo aquele aquela já também só muito é são era eram foi ser sou
    somos está estão estou estava estar tem têm tenho tinha ter vai vou vamos há pode posso podem poder devo deve devem
    algum alguma alguns algumas nenhum nenhuma cada outro outra gente ok obrigado obrigada oi olá sim beleza valeu"""),
"id": _folded("""
    yang dan di ke dari ini itu dengan untuk tidak tak ada akan juga atau pada adalah ialah saya aku kamu engkau dia ia
    mereka kita kami anda sudah telah belum bisa dapat harus sedang masih lebih sangat apa siapa kapan mana bagaimana
    mengapa kenapa berapa apakah yaitu yakni oleh karena jika kalau agar supaya tetapi tapi namun hanya saja pun lagi
    para sebuah seorang setiap tiap semua banyak sedikit nya ya oke ok terima kasih makasih halo hai sip seperti
    sebaiknya punya mau ingin boleh perlu nih dong kok sih deh"""),
"ar": _folded("""
    في من إلى الى على عن عمن مع هذا هذه ذلك تلك هؤلاء هو هي هم هن هما أنا نحن أنت أنتم كان كانت يكون تكون كانوا ما
    ماذا متى أين كيف لماذا لم لن لا ليس ليست قد كل بعض غير بين عند حتى إذا إن أن أو ثم بل لكن التي الذي الذين يتم تم
    لدى لديه لديها لدي لديك له لها لهم لك لي به بها فيه فيها منه منها عليه عليها و ف ب ل ك يا أيضا فقط جدا شكرا
    مرحبا حسنا تمام نعم أهلا هل أي كم يمكن يجب يفعل تفعل يقوم تقوم"""),
"hi": _folded("""
    का की के को में से पर है हैं था थी थे हो होता होती होते होगा होगी होंगे और या भी तो ही न नहीं यह वह ये वे वो इस उस
    इन उन इसे उसे मैं हम तुम आप मेरा मेरी मेरे हमारा हमारी हमारे तुम्हारा तेरा आपका आपकी आपके उसका उसकी उसके उनका
    उनकी उनके इसका इसकी इसके कौन क्या कब कहाँ कहां कैसे क्यों कितना कितनी कितने किस किसे किसी कोई कुछ एक लिए साथ बाद
    पहले जो जब तक अब यहाँ वहाँ सकता सकती सकते रहा रही रहे गया गई गए कर करता करती करते करना करें किया हाँ ठीक
    धन्यवाद नमस्ते शुक्रिया अच्छा ओके तरह वाला वाली वाले बहुत"""),
"bn": _folded("""
    এবং ও আর বা কিন্তু যে যা যিনি যারা এই ওই সেই এটা ওটা সেটা এটি ওটি সেটি সে তারা আমি আমরা তুমি তোমরা আপনি আপনারা
    তিনি তাঁরা আমার আমাদের তোমার তোমাদের আপনার আপনাদের তার তাদের তাঁর তাঁদের কে কী কি কেন কোন কোথায় কখন কবে কীভাবে
    কিভাবে কেমন কত কতটা কাকে কার একটি একটা এক জন্য সাথে সঙ্গে থেকে দিয়ে নিয়ে হয় হয়ে হলে হবে হচ্ছে ছিল ছিলেন আছে
    আছেন নেই না নয় হ্যাঁ ঠিক আচ্ছা ধন্যবাদ হ্যালো নমস্কার প্রতি করে করেন করা করছে যায় খুব এর এ তে র"""),
}
STOPWORDS = frozenset().union(*STOP.values())

NEGATORS = _folded("""not no never nobody nothing cannot don't doesn't didn't isn't aren't wasn't weren't won't can't
    couldn't shouldn't haven't hasn't dont doesnt didnt isnt cant wont не нет никогда ни nunca jamás tampoco nadie
    ningún ninguna ninguno ne pas jamais aucun aucune non não nem nenhum nenhuma tidak tak bukan belum jangan لا لم لن
    ليس ليست नहीं न मत না নয় নেই নি""")


def _greeting_key(text: str) -> str:
    folded = _fold_text(unicodedata.normalize("NFC", text or "").lower())
    return "".join(c for c in folded if c.isalnum() or unicodedata.category(c)[0] == "M")


# Whole-message greetings and courtesies: they name a time of day ("good morning") but ask nothing, so they must not
# recall facts that mention mornings. Compared without case, accents, punctuation and spaces.
GREETINGS = frozenset(_greeting_key(g) for g in """
    goodmorning|goodafternoon|goodevening|goodnight|morning|gm|gn|hello|hi|hey|hiall|hellothere|hithere|howareyou|
    thankyou|thanksalot|thankssomuch|
    доброеутро|добрыйдень|добрыйвечер|спокойнойночи|добройночи|утро|привет|приветик|всемпривет|здравствуй|здравствуйте|
    какдела|спасибо|спасибобольшое|благодарю|
    早上好|早安|上午好|中午好|下午好|晚上好|晚安|你好|您好|大家好|嗨|谢谢|多谢|非常感谢|
    hola|buenos días|buen día|buenas|buenas tardes|buenas noches|hola a todos|qué tal|gracias|muchas gracias|mil gracias|
    bonjour|bonsoir|salut|coucou|bonne nuit|bonne journée|bonne soirée|bonjour à tous|salut à tous|ça va|merci|
    merci beaucoup|
    olá|oi|bom dia|boa tarde|boa noite|olá a todos|oi pessoal|tudo bem|obrigado|obrigada|muito obrigado|muito obrigada|
    halo|hai|selamat pagi|selamat siang|selamat sore|selamat malam|halo semua|apa kabar|terima kasih|makasih|
    terima kasih banyak|
    مرحبا|أهلا|أهلا وسهلا|السلام عليكم|صباح الخير|مساء الخير|تصبح على خير|كيف حالك|شكرا|شكرا جزيلا|
    नमस्ते|नमस्कार|हैलो|हेलो|सुप्रभात|शुभ प्रभात|शुभ रात्रि|आप कैसे हैं|धन्यवाद|बहुत धन्यवाद|शुक्रिया|
    নমস্কার|হ্যালো|শুভ সকাল|সুপ্রভাত|শুভ রাত্রি|আসসালামু আলাইকুম|কেমন আছেন|কেমন আছো|ধন্যবাদ|অনেক ধন্যবাদ""".split("|"))


def is_greeting(text: str) -> bool:
    return _greeting_key(text) in GREETINGS


def is_identifier(token: str) -> bool:
    """'pve-01', 'v0.21.6', 'gpt-4o', 'host:22', '/etc/hosts': kept exact, never stemmed or dropped. A hyphen alone
    does not make one: "prend-il", "anak-anak" and "sexta-feira" are words."""
    return any(c.isdigit() for c in token) or (len(token) > 2 and any(c in token for c in "._:/"))


# -- stemming ------------------------------------------------------------------------------------------------------

# Arabic light10 (Larkey, Ballesteros and Connell; the stemmer Lucene ships): one prefix, then each suffix in turn,
# leaving at least two letters. Input is folded, so ة is already ه.
_AR_PREFIXES = ("ال", "وال", "بال", "كال", "فال", "لل", "و")
_AR_SUFFIXES = ("ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي")


def _arabic_light(word: str) -> str:
    for prefix in _AR_PREFIXES:
        if word.startswith(prefix) and len(word) >= (4 if prefix == "و" else len(prefix) + 2):
            word = word[len(prefix):]
            break
    for suffix in _AR_SUFFIXES:
        if word.endswith(suffix) and len(word) >= len(suffix) + 2:
            word = word[:-len(suffix)]
    return word


# Bengali: Snowball has no stemmer; one inflectional suffix (case, plural, classifier, common verb endings) is
# stripped, longest first, leaving at least two characters. Over-stripping a bare word ("শুক্রবার" -> "শুক্রবা") is
# harmless because a word also matches by its unstripped form.
_BN_SUFFIXES = sorted({fold(unicodedata.normalize("NFC", s)) for s in """
    গুলোকে গুলোতে গুলোর গুলো গুলিকে গুলিতে গুলির গুলি দেরকে দের েরা রা টাকে টিকে টাতে টিতে টার টির টা টি খানা খানি
    য়েরা য়ের য়ে য় ের কে তে েতে র ে এর এ চ্ছিলাম চ্ছিলেন চ্ছিল চ্ছেন চ্ছিস চ্ছি চ্ছে ছিলাম ছিলেন ছিল ছেন ছিস ছি ছে
    েছিলাম েছিলেন েছেন েছি েছে লাম লেন বেন বে বো তাম তেন ওয়ার ওয়া ানো""".split()}, key=len, reverse=True)


def _bengali_stem(word: str) -> str:
    for suffix in _BN_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[:-len(suffix)]
    return word


_DEFAULT_LANG = {"latin": "en", "cyrillic": "ru", "arabic": "ar", "devanagari": "hi", "bengali": "bn"}


@lru_cache(maxsize=65536)
def stem(word: str, lang: str | None = None) -> str:
    """Stem of a folded, lower-cased word in *lang* (by its script when None); the word itself for identifiers, scripts
    without a stemmer, or without snowballstemmer."""
    if is_identifier(word):
        return word
    lang = lang or _DEFAULT_LANG.get(word_script(word))
    if lang == "ar":
        return _arabic_light(word)
    if lang == "bn":
        return _bengali_stem(word)
    stemmer = _STEMMERS.get(lang)
    return fold(stemmer.stemWord(word)) if stemmer else word


@lru_cache(maxsize=65536)
def _keys(word: str, langs: tuple[str, ...]) -> frozenset:
    keys = {word, *(stem(word, lang) for lang in langs)}
    if "ar" in langs:
        if "ar" in _STEMMERS:
            keys.add(fold(_STEMMERS["ar"].stemWord(word)))
        # a one-letter conjunction or preposition (و ف ب ك ل) before a name or a short word: "لإيفا" is "for Eva"
        if word[0] in "وفبكل" and len(word) >= 4:
            bare = word[1:]
            keys |= {bare, _arabic_light(bare)}
    return frozenset(keys)


# -- tokenisation --------------------------------------------------------------------------------------------------

# French/Italian elision: "d'Eva", "l'offre", "qu'on" are Eva, offre, on.
_ELISION = re.compile(r"^(?:l|d|j|m|n|s|t|c|qu|jusqu|lorsqu|puisqu|quoiqu|dell|all|nell|sull|dall|un)'(?=[^\W\d_])")
# French pronouns after an inverted verb: "prend-il", "a-t-elle", "est-ce", "vas-y".
_CLITICS = frozenset("je tu il elle on nous vous ils elles ce moi toi lui leur le la les y en".split())


def _edge(c: str) -> bool:
    return c in PUNCT or (not c.isascii() and unicodedata.category(c)[0] == "P")


def _strip_edges(token: str) -> str:
    i, j = 0, len(token)
    while i < j and _edge(token[i]):
        i += 1
    while j > i and _edge(token[j - 1]):
        j -= 1
    return token[i:j]


def _split_word(token: str) -> list[tuple[str, bool]]:
    """(word, bag-only) pieces of a hyphenated token: split where the script changes ("hr-ovh-এর" is hr-ovh + এর,
    "IT-тусовка" is it + тусовка); reduplication is the word ("anak-anak"); French inversion loses its pronoun
    ("prend-il"); other hyphenated words stay whole and lend their parts to containment checks ("sextas-feiras")."""
    if "-" not in token or UNSEGMENTED.search(token):
        return [(token, False)]
    groups: list[list] = []
    for part in token.split("-"):
        script = word_script(part)
        script = "latin" if script == "common" else script
        if groups and groups[-1][0] == script:
            groups[-1][1].append(part)
        else:
            groups.append([script, [part]])
    out = []
    for _, parts in groups:
        original = "-".join(parts)
        kept = parts
        if len(parts) > 1 and not is_identifier(original) and all(parts):
            if len(set(parts)) == 1:
                kept = parts[:1]
            else:
                while len(kept) > 1 and kept[-1] in _CLITICS:
                    kept = kept[:-1]
                if len(kept) > 1 and kept[-1] == "t":
                    kept = kept[:-1]
        whole = "-".join(kept)
        out.append((whole, False))
        if "-" in original and not is_identifier(original):  # the whole and its parts, for containment
            out += [(original, True)] * (original != whole) + [(p, True) for p in parts if p]
    return out


@dataclass(frozen=True)
class Word:
    text: str  # folded, lower-cased
    stem: str  # primary stem (the first language's)
    keys: frozenset  # the word and its stems
    stop: bool
    identifier: bool
    scan: bool  # the FTS5 index cannot find its other forms (see _SCAN_SCRIPTS; Indonesian prefixes)


@dataclass(frozen=True)
class Analysis:
    words: tuple  # Word, in order
    bag: frozenset  # folded words, plus the parts of hyphenated ones
    keys: frozenset  # every key of every word in the bag
    text: str  # folded, lower-cased text, for boundary matches
    langs: tuple  # languages of the Latin words (all five when undetermined)


def _latin_langs(words: list[str]) -> tuple[tuple[str, ...], bool]:
    """Languages of the Latin words by their function words; ties keep every tied language; none found: all five."""
    votes = {lang: sum(w in STOP[lang] for w in words) for lang in LATIN}
    best = max(votes.values())
    return (tuple(lang for lang in LATIN if votes[lang] == best), True) if best else (LATIN, False)


@lru_cache(maxsize=1024)
def analyze(text: str) -> Analysis:
    lowered = unicodedata.normalize("NFC", text or "").lower().replace("’", "'")
    pieces = []
    for token in lowered.split():
        token = _ELISION.sub("", _strip_edges(token))
        for piece, part in _split_word(token) if token else ():
            piece = fold(_strip_edges(piece))
            if piece:
                pieces.append((piece, part))
    latin_words = [p for p, part in pieces if not part and word_script(p) == "latin" and not is_identifier(p)]
    latin, determined = _latin_langs(latin_words)
    words, bag, keys = [], set(), set()
    for piece, part in pieces:
        script, ident = word_script(piece), is_identifier(piece)
        langs = () if ident or UNSEGMENTED.search(piece) else latin if script == "latin" else _SCRIPT_LANGS.get(script, ())
        word_keys = _keys(piece, langs) if langs else frozenset({piece})
        bag.add(piece)
        keys |= word_keys
        if not part:
            words.append(Word(
                text=piece, stem=stem(piece, langs[0]) if langs else piece, keys=word_keys,
                stop=not ident and any(piece in STOP[lang] for lang in langs), identifier=ident,
                scan=script in _SCAN_SCRIPTS or (script == "latin" and determined and "id" in langs)))
    return Analysis(tuple(words), frozenset(bag), frozenset(keys), _fold_text(lowered), latin)


def name_keys(name: str) -> tuple[frozenset, ...]:
    """Keys of each word of an entity name under every language of its script: a name is matched in a message of any
    of them."""
    out = []
    for word in analyze(name).words:
        script = word_script(word.text)
        langs = () if word.identifier or UNSEGMENTED.search(word.text) else \
            LATIN if script == "latin" else _SCRIPT_LANGS.get(script, ())
        out.append(_keys(word.text, langs) if langs else frozenset({word.text}))
    return tuple(out)


def words(text: str) -> list[str]:
    """Folded, lower-cased words with surrounding punctuation stripped."""
    return [w.text for w in analyze(text).words]


def stems(text: str) -> set[str]:
    """Every comparison key of the text's words."""
    return set(analyze(text).keys)


def content_keys(text: str) -> list[frozenset]:
    """Key sets of the informative words (stopwords dropped), distinct; unsegmented runs give their content n-grams.
    Compared with soft_overlap, so two texts whose language was told apart differently (one has a function word, the
    other none) still meet on a shared stem."""
    out = []
    for w in analyze(text).words:
        if UNSEGMENTED.search(w.text):
            out += [frozenset({g}) for g, weight in run_grams(w.text) if weight]
        elif w.identifier or (not w.stop and len(w.text) > 1):
            out.append(w.keys)
    return list(dict.fromkeys(out))


def soft_overlap(a: list[frozenset], b: list[frozenset]) -> tuple[int, int]:
    """(words of a found in b, words of b found in a); two words match when their keys meet."""
    return (sum(1 for x in a if any(not x.isdisjoint(y) for y in b)),
            sum(1 for y in b if any(not y.isdisjoint(x) for x in a)))


def soft_jaccard(a: list[frozenset], b: list[frozenset]) -> float:
    if not a or not b:
        return 0.0
    shared = min(soft_overlap(a, b))
    return shared / (len(a) + len(b) - shared)


def content_stems(text: str) -> set[str]:
    """Stems of the informative words only (stopwords dropped); runs of unsegmented scripts contribute their
    content n-grams, so Chinese or Thai text compares by fragments rather than as one huge "word"."""
    out = set()
    for w in analyze(text).words:
        if UNSEGMENTED.search(w.text):
            out |= {g for g, weight in run_grams(w.text) if weight}
        elif w.identifier or (not w.stop and len(w.text) > 1):
            out.add(w.stem)
    return out


# Scripts written without spaces between words (Thai, Lao, Khmer, Myanmar, Chinese, Japanese) and Korean, whose
# particles attach to the word: the FTS5 unicode61 tokenizer indexes a whole run as one token, so these are matched
# by character n-grams against the text (bigrams for CJK and Hangul, trigrams for Southeast Asian scripts).
UNSEGMENTED = re.compile(r"[฀-໿က-႟ក-៿]+|[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")
_SEA = re.compile(r"[฀-໿က-႟ក-៿]")
# Chinese function characters (particles, pronouns, question words, auxiliaries). A gram made only of them (可以, 什么,
# 怎么, 我们) carries no topic; a gram with one of them carries half; others carry full weight.
CJK_FUNCTION = frozenset("的是了吗呢吧啊呀哦嗯么怎什哪谁几多少在有和与及或就都也还要会能可以把被给让对为从到这那你我他她它们个一不没很太该应些吗嘛")
CJK_NEGATORS = frozenset("不没无别非未")
_MAX_GRAMS = 32
# Bengali, Arabic-Indic, Persian and Devanagari digits: the FTS5 index keeps them as written, so a number is also
# searched in each of them ("2027" finds "২০২৭"); folding makes the comparison itself digit-agnostic.
_NATIVE_DIGITS = tuple(str.maketrans("0123456789", d) for d in ("০১২৩৪৫৬৭৮৯", "٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹", "०१२३४५६७८९"))
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


@dataclass(frozen=True)
class Term:
    """One informative query term: the folded word, its primary stem, whether it is an identifier, the FTS-safe
    tokens it is searched as (operator characters removed), its comparison keys, and whether it is matched by
    scanning the facts' text instead of through the FTS5 index."""
    word: str
    stem: str
    identifier: bool
    tokens: tuple[str, ...] = ()
    keys: frozenset = frozenset()
    scan: bool = False

    @property
    def loose(self) -> str:
        """A shorter shared beginning for long words, so derivations Snowball keeps apart still meet
        ("переезжаем" / "переезд" share "переез"); "" for short words and identifiers."""
        if self.identifier or len(self.tokens) != 1 or len(self.stem) < _LOOSE_FROM:
            return ""
        return self.stem[:max(_LOOSE_MIN, len(self.stem) - 2)]

    def fts(self, operators: dict | None = None) -> str:
        """FTS5 expression built only from operator-free tokens: the exact word (a phrase when it splits into
        several tokens), OR each of its keys as a prefix when the word is one plain token."""
        tokens = self.tokens or tuple(self.word.translate(FTS_OPERATORS).split())
        exact = '"' + " ".join(tokens) + '"'
        if self.identifier or len(tokens) != 1 or tokens[0] != self.word:
            if not any(t.isascii() and t.isdigit() for t in tokens):
                return exact
            native = ['"' + " ".join(t.translate(d) if t.isascii() and t.isdigit() else t for t in tokens) + '"'
                      for d in _NATIVE_DIGITS]
            return "(" + " OR ".join([exact, *native]) + ")"
        prefixes = sorted(k for k in (self.keys or {self.stem}) | ({self.loose} if self.loose else set())
                          if len(k) >= _MIN_STEM and k.translate(FTS_OPERATORS) == k and k.split() == [k])
        parts = [f'"{p}"*' for p in prefixes] + ([] if self.word in prefixes else [exact])
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    @cached_property
    def _bounded(self) -> re.Pattern:
        # An ASCII identifier is bounded by ASCII word characters only: in Chinese text "hr-ovh" is glued to the
        # characters around it ("服务器hr-ovh运行").
        edge = r"A-Za-z0-9_\-" if self.word.isascii() else r"\w\-"
        return re.compile(rf"(?<![{edge}])" + re.escape(self.word) + rf"(?![{edge}])")

    def found_in(self, fact: Analysis) -> bool:
        """Does a fact contain this term: the same word, a shared key (word or stem), the loose beginning, or (for
        identifiers and multi-token words) the same token run with word boundaries, so "pve-01" does not match
        inside "pve-010"."""
        if self.word in fact.bag:
            return True
        if not self.identifier and len(self.tokens) <= 1 and not self.keys.isdisjoint(fact.keys):
            return True
        if self.loose and self.loose in fact.text and any(w.startswith(self.loose) for w in fact.bag):
            return True
        return self.word in fact.text and self._bounded.search(fact.text) is not None


def query_terms(text: str) -> list[Term]:
    """Distinct informative terms of a natural-language query, in order of appearance."""
    seen, terms = set(), []
    for w in analyze(text).words:
        if w.stop or (not w.identifier and len(w.text) < 2):
            continue
        tokens = tuple(w.text.translate(FTS_OPERATORS).split())
        if not tokens:
            continue
        single = len(tokens) == 1
        key = w.stem if single else w.text
        if key not in seen:
            seen.add(key)
            terms.append(Term(w.text, key, w.identifier, tokens, w.keys if single else frozenset({w.text}), w.scan))
    return terms
