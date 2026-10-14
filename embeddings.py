"""Search by meaning: optional, online, off by default. An OpenAI-compatible embeddings endpoint the user names (OpenAI,
Gemini's OpenAI-compatible API, OpenRouter, or a local server such as Ollama or Text Embeddings Inference) turns facts
and messages into vectors, and recall uses them next to the words, never in their place:

- a fact's vector is made after the turn, on the core's background worker (``sync_turn``), never inside a write: a
  network call must not hold the database or the provider lock;
- vectors live in their own table, ``fact_embeddings``: one per current fact, with the model and a hash of the text it
  was made from, so a text an older version changed, or a vector of another model, is never compared;
- every failure falls back to words without a word of complaint to the user; after one the endpoint is left alone for
  a while (60 s, doubling up to 15 min), so a dead server does not cost seconds on every turn;
- what recall must not inject (``_recall_problem``) is never sent, nor a message holding a credential.

Nothing here runs, and nothing leaves the machine, unless ``embeddings: on``. Design after
itpartypattaya/hermes-plugin-holographic#3 by njwangfeng."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import logging
import math
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional, as for HRR: without it the feature stays off
    np = None

from .text import UNSEGMENTED, is_greeting, query_terms

logger = logging.getLogger(__name__)

TEXT_MAX_CHARS = 2000  # a fact holds at most 1000; a message is cut here before it is sent
BATCH = 32  # texts per request
_PAUSE_FIRST, _PAUSE_MAX = 60.0, 900.0  # seconds the endpoint is left alone after a failure, doubling
_QUERY_CACHE = 64  # message vectors kept: prefetch and a fact_store search of the same text make one call
_MAX_RESPONSE = 64 << 20
# How far above this message's usual similarity to the store's facts a fact must stand to be offered on meaning alone,
# in standard deviations over what the closest of N unrelated facts reaches by chance (sqrt(2 ln N)): a paraphrase of a
# stored fact stands out, chit-chat and off-topic questions mostly do not, and the bar rises with the store as the
# chance maximum does. Calibrated 2026-10-14 on 100 synthetic facts in five languages, 67 paraphrases sharing no
# words with their fact and 45 questions that must recall nothing, on text-embedding-3-small and gemini-embedding-001
# (stores of 30, 50, 100): margin 1.0 recalls 79-93 % of paraphrases at 100 facts with no chit-chat and 2 of 22
# off-topic questions (both near a stored topic). Absolute similarity differs by model (a paraphrase scores 0.38-0.66
# on the first, 0.68-0.84 on the second), so it is only an optional floor (embedding_min_score). Below
# MIN_VECTORS_FOR_Z vectors the spread says nothing and only that floor can admit a fact.
MIN_REQUEST_SECONDS = 0.5  # a request is not started with less time than this left
RECALL_MARGIN = 1.0  # before a turn
SEARCH_MARGIN = 0.5  # fact_store search: the agent asked, and judges what it gets
MIN_VECTORS_FOR_Z = 20


def z_bar(count: int, margin: float) -> float:
    """The z a fact needs among *count* vectors: the chance maximum of that many plus *margin*."""
    return math.sqrt(2.0 * math.log(max(count, 2))) + margin


def _now() -> float:
    return time.monotonic()


def text_hash(text: str) -> str:
    """What a vector was made from: a different text (a change, an older version's edit) is a different hash."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


def to_blob(vector) -> bytes:
    return np.asarray(vector, dtype="<f4").tobytes()


def _number(value, default, low, high, kind=float):
    try:
        if value is None or str(value).strip() == "":
            return default
        return min(high, max(low, kind(value)))
    except (TypeError, ValueError):
        return default


class Settings:
    """The plugin config's embedding_* keys, validated (see README, "Search by meaning")."""

    __slots__ = ("url", "model", "key_env", "dimensions", "timeout", "min_score", "recall_facts", "weight", "space")

    def __init__(self, config: dict):
        self.url = str(config.get("embedding_url") or "https://api.openai.com/v1").strip()
        self.model = str(config.get("embedding_model") or "text-embedding-3-small").strip()
        self.key_env = str(config.get("embedding_key_env") or "OPENAI_API_KEY").strip()
        self.dimensions = _number(config.get("embedding_dimensions"), None, 1, 65536, int)
        # under the core's bound on an external provider's prefetch (8 s), with room for the words
        self.timeout = _number(config.get("embedding_timeout"), 3.0, 0.5, 6.0)
        self.min_score = _number(config.get("embedding_min_score"), None, -1.0, 1.0)
        self.recall_facts = _number(config.get("embedding_prefetch_facts"), 1, 0, 3, int)
        self.weight = _number(config.get("embedding_weight"), 0.3, 0.0, 0.8)
        # a label of your own, part of the vectors' identity: change it when the server starts serving another model
        # under the same address and name, and every vector is made again
        self.space = str(config.get("embedding_space") or "").strip()

    @property
    def tag(self) -> str:
        """Which vectors may be compared, as stored next to each: the model at this address (one model name may mean
        two models on two servers), with the dimension when one was asked for. Host, port and path only: no user
        name or password in the address; the path keeps its case; a query string (a deployment, or a key) by a short
        hash of it, never as written. A server that starts serving another model under the same address and name
        cannot be told apart: set or change embedding_space then, and every vector is made again."""
        try:
            parts = urllib.parse.urlsplit(self.url)
            where = (f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}"
                     + (f":{parts.port}" if parts.port else "") + parts.path.rstrip("/"))
            if parts.query:
                where += "?" + hashlib.sha256(parts.query.encode("utf-8")).hexdigest()[:8]
        except ValueError:
            where = "?"
        return (f"{where}/{self.model}" + (f"@{self.dimensions}" if self.dimensions else "")
                + (f"#{self.space}" if self.space else ""))


def settings_from(config: dict) -> Settings | None:
    """Settings when ``embeddings`` is on, else None (the default: no vectors, no calls)."""
    value = config.get("embeddings", "off")
    on = value is True or str(value).strip().lower() in {"on", "true", "yes", "1"}
    return Settings(config) if on else None


def _local(host: str) -> bool:
    """A host on this machine or the local network: plain http (and no key) is allowed to it. An address is judged as
    an address first (a public IPv6 one has no dot either); a name by its form, not by what it resolves to."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return (host == "localhost" or "." not in host  # a container or LAN name
                or host.endswith((".localhost", ".local", ".lan", ".internal", ".home.arpa")))
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _endpoint(url: str) -> tuple[str | None, bool, str]:
    """(the /embeddings address, whether it is local, "") or (None, False, why it is refused)."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None, False, "embedding_url is not a valid address"
    if parts.scheme not in ("https", "http") or not host:
        return None, False, "embedding_url is not an http(s) address"
    local = _local(host)
    if parts.scheme == "http" and not local:
        return None, False, "embedding_url sends plain http to a public host: use https"
    path = parts.path.rstrip("/")
    if not path.endswith("/embeddings"):
        path += "/embeddings"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, "")), local, ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the Authorization header to another address: it is a failure instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _vectors(payload, count: int, dimensions: int | None = None) -> list:
    """Unit-length float32 vectors from an OpenAI-style answer, in input order; ValueError when it is not one: another
    number of vectors, indexes that are not exactly 0 … count-1, vectors of different sizes or not of the size asked
    for, anything not a finite number, a zero vector."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != count:
        raise ValueError("the answer has a different number of vectors than texts sent")
    indexes = [item.get("index") if isinstance(item, dict) else None for item in data]
    if any(i is not None for i in indexes):
        # A missing index is 0: Gemini's OpenAI-compatible API, like other protobuf-backed servers, leaves a field at
        # its default out (measured 2026-10-14). Two missing ones are two zeros, and refused.
        indexes = [0 if i is None else i for i in indexes]
        if sorted(i if isinstance(i, int) and not isinstance(i, bool) else -1 for i in indexes) != list(range(count)):
            raise ValueError("the answer's vector indexes do not match the texts sent")
        data = [item for _, item in sorted(zip(indexes, data), key=lambda pair: pair[0])]
    out, size = [], None
    for item in data:
        values = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(values, list) or not values:
            raise ValueError("a vector is missing from the answer")
        try:
            vector = np.asarray(values, dtype=np.float64)  # float64: squares of large float32 values overflow
        except (TypeError, ValueError, OverflowError):
            raise ValueError("a vector holds something that is not a number") from None
        if vector.ndim != 1 or (size is not None and vector.shape[0] != size) or not np.all(np.isfinite(vector)):
            raise ValueError("a vector is malformed")
        if dimensions and vector.shape[0] != dimensions:
            raise ValueError("a vector has another size than embedding_dimensions asks for")
        scale = float(np.max(np.abs(vector)))
        if scale == 0.0:
            raise ValueError("a vector is all zeros")
        vector = vector / scale  # then the norm cannot overflow or underflow
        unit = (vector / float(np.linalg.norm(vector))).astype(np.float32)
        if not np.all(np.isfinite(unit)) or abs(float(np.linalg.norm(unit)) - 1.0) > 1e-3:
            raise ValueError("a vector cannot be brought to unit length")
        size = unit.shape[0]
        out.append(unit)
    return out


def _read_until(response, deadline: float) -> bytes:
    """The body, read a piece at a time: the socket timeout bounds each read, not the whole, so a server that sends a
    few bytes before every timeout is cut off at *deadline*, and a body past _MAX_RESPONSE is refused."""
    chunks, total = [], 0
    while True:
        if _now() > deadline:
            raise TimeoutError("the answer took longer than embedding_timeout")
        chunk = response.read1(65536) if hasattr(response, "read1") else response.read(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > _MAX_RESPONSE:
            raise ValueError("the answer is too large")
        chunks.append(chunk)


class Embedder:
    """The endpoint: texts in, unit-length float32 vectors out. Shared by every store a provider opens; thread-safe."""

    def __init__(self, settings: Settings, environ=None):
        import os
        self.settings = settings
        self._environ = os.environ if environ is None else environ
        self._url, self._local, self._refused = _endpoint(settings.url)
        self._lock = threading.Lock()
        self._pause = self._paused_until = 0.0
        self._last_error = ""
        self._queries: OrderedDict = OrderedDict()
        self.calls = 0  # requests sent (stats, tests)

    def _key(self) -> str:
        return str(self._environ.get(self.settings.key_env) or "").strip() if self.settings.key_env else ""

    def unavailable(self) -> str:
        """Why the endpoint cannot be used at all ("" when it can): numpy, the address, the key. Never the key's value:
        a key with a line break inside would make the HTTP library raise an error quoting the header."""
        if np is None:
            return "numpy is not installed"
        if self._refused:
            return self._refused
        key = self._key()
        if not key and not self._local:
            return f"{self.settings.key_env} is not set"
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in key):
            return f"the value of {self.settings.key_env} has a space or a control character inside"
        return ""

    def status(self) -> str:
        why = self.unavailable()
        if why:
            return f"unavailable: {why}"
        with self._lock:
            left = self._paused_until - _now()
            if left > 0:
                return f"paused after an error ({self._last_error}); tried again in {int(left) + 1} s"
        return "ready"

    def usable(self) -> bool:
        return self.status() == "ready"

    def embed(self, texts: list[str], budget: float | None = None) -> list | None:
        """Vectors of *texts* (each cut to TEXT_MAX_CHARS), or None: unusable, or a request failed (logged; paused).
        *budget*: seconds the caller can still wait, for every request together (each may use all that is left);
        None: embedding_timeout for each."""
        if not texts or not self.usable():
            return None
        out: list = []
        deadline = None if budget is None else _now() + budget
        for start in range(0, len(texts), BATCH):
            left = None if deadline is None else deadline - _now()
            if left is not None and left < MIN_REQUEST_SECONDS:
                return None
            got = self._request([t[:TEXT_MAX_CHARS] for t in texts[start:start + BATCH]], left)
            if got is None:
                return None
            out += got
        return out

    def query(self, text: str):
        """The vector of a message, from a small cache when it was asked for a moment ago; None when unavailable."""
        key = (text or "")[:TEXT_MAX_CHARS]
        with self._lock:
            if key in self._queries:
                self._queries.move_to_end(key)
                return self._queries[key]
        got = self.embed([key])
        if got is None:
            return None
        with self._lock:
            self._queries[key] = got[0]
            while len(self._queries) > _QUERY_CACHE:
                self._queries.popitem(last=False)
        return got[0]

    def _request(self, batch: list[str], budget: float | None = None) -> list | None:
        """One request. It runs on a daemon thread and the caller waits *budget* seconds — embedding_timeout when none
        is given (a message's vector before a turn), the remaining after-turn budget for fact vectors, which nobody
        waits for and come in batches of 32 — and no longer: urllib's timeout bounds each socket operation, so a server that sends its headers or its
        chunks a byte at a time could otherwise hold the caller — and the provider lock prefetch holds — far longer
        (review 2026-10-14). A request given up on finishes or fails on its own thread; the endpoint is paused."""
        timeout = self.settings.timeout if budget is None else max(0.1, budget)
        body = {"model": self.settings.model, "input": batch}
        if self.settings.dimensions:
            body["dimensions"] = self.settings.dimensions
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "hermes-plugin-holographic"}
        key = self._key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(self._url, data=json.dumps(body).encode("utf-8"), headers=headers,
                                         method="POST")
        self.calls += 1
        deadline = _now() + timeout
        box: dict = {}

        def send():  # decoding too, so the caller's wait covers the whole of it
            try:
                with _OPENER.open(request, timeout=timeout) as response:
                    raw = _read_until(response, deadline)
            except BaseException as exc:  # handed to the caller below
                box["error"] = exc
                return
            try:
                box["vectors"] = _vectors(json.loads(raw), len(batch), self.settings.dimensions)
            except ValueError as exc:  # our own wording, or the JSON parser's position
                box["malformed"] = str(exc)[:120]

        worker = threading.Thread(target=send, name="holographic-embeddings", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            self._failed("no answer (timeout)")
            return None
        # What is logged and shown in stats is a category, never the text of an exception raised while sending: it
        # may quote the request (a header with the key in it).
        try:
            if "error" in box:
                raise box["error"]
        except urllib.error.HTTPError as exc:  # the body may echo the request: not read
            # A model that cannot shorten its vectors (bge-m3 behind an aggregator, reported on 0.14.1) answers 400 to
            # the `dimensions` field rather than ignoring it: name the likely cause instead of a bare code.
            hint = (" — embedding_dimensions is set; a model that cannot shorten its vectors rejects it, leave it unset"
                    if exc.code == 400 and self.settings.dimensions else "")
            self._failed(f"HTTP {exc.code}{hint}")
            return None
        except urllib.error.URLError as exc:
            self._failed(f"cannot reach the endpoint ({type(exc.reason).__name__})")
            return None
        except (OSError, ValueError, http.client.HTTPException) as exc:  # timeout, reset, a broken answer
            self._failed(f"no answer ({type(exc).__name__})")
            return None
        except Exception as exc:  # anything else raised while sending: a category, never its text
            self._failed(f"no answer ({type(exc).__name__})")
            return None
        if "malformed" in box:
            self._failed(f"malformed answer: {box['malformed']}")
            return None
        vectors = box["vectors"]
        self._recovered()
        return vectors

    def _failed(self, why: str) -> None:
        with self._lock:
            first = not self._last_error
            self._pause = min(_PAUSE_MAX, self._pause * 2) if self._pause else _PAUSE_FIRST
            self._paused_until = _now() + self._pause
            self._last_error = why
            pause = self._pause
        (logger.warning if first else logger.debug)(
            "holographic: embeddings endpoint failed (%s); words only for %d s", why, int(pause))

    def _recovered(self) -> None:
        with self._lock:
            was = self._last_error
            self._pause = self._paused_until = 0.0
            self._last_error = ""
        if was:
            logger.info("holographic: embeddings endpoint works again")


def worth_asking(query: str, *, before_turn: bool) -> bool:
    """Is the message worth a call to the endpoint? Never a greeting or a thank-you. Before a turn it needs two words
    that carry meaning, or five characters of Chinese, Japanese, Korean or Thai: short chit-chat sits on the similarity
    noise floor of every store, and only its length tells it apart (#3). An explicit search needs one."""
    text = (query or "").strip()
    if not text or is_greeting(text):
        return False
    glued = sum(len(run) for run in UNSEGMENTED.findall(text))
    words = sum(1 for t in query_terms(UNSEGMENTED.sub(" ", text)) if t.word)
    return words >= (2 if before_turn else 1) or glued >= (5 if before_turn else 2)


class Meaning:
    """How close each current fact is to one message (cosine of unit vectors), and how unusual that closeness is for
    this message over the whole store."""

    __slots__ = ("sims", "neutral", "mean", "std", "weight")

    def __init__(self, ids: list[int], sims, weight: float):
        self.sims = {fact_id: float(s) for fact_id, s in zip(ids, sims)}
        self.neutral = float(np.median(sims)) if len(ids) else 0.0  # a fact without a vector neither gains nor loses
        self.mean = float(np.mean(sims)) if len(ids) else 0.0
        self.std = float(np.std(sims)) if len(ids) else 0.0
        self.weight = weight

    def cos(self, fact_id: int) -> float:
        return self.sims.get(fact_id, self.neutral)

    def z(self, fact_id: int) -> float:
        s = self.sims.get(fact_id)
        return (s - self.mean) / self.std if s is not None and self.std > 1e-9 else 0.0

    def standout(self, fact_id: int, margin: float, min_score: float | None) -> bool:
        """Close enough to be offered on meaning alone: above embedding_min_score when it is set, and, with enough
        vectors to judge, above z_bar(count, margin)."""
        s = self.sims.get(fact_id)
        if s is None or (min_score is not None and s < min_score):
            return False
        if len(self.sims) >= MIN_VECTORS_FOR_Z:
            return self.z(fact_id) >= z_bar(len(self.sims), margin)
        return min_score is not None

    def ranked(self) -> list[tuple[int, float]]:
        return sorted(self.sims.items(), key=lambda item: (-item[1], item[0]))


class VectorIndex:
    """One store's fact vectors of one model, kept between queries: a vector is read from the file again only when its
    fact is new, its text changed or it was made again, and the matrix is rebuilt only when that set changes.
    Thread-safe (the catch-up after a turn runs on the core's background worker)."""

    def __init__(self, store, tag: str):
        self.store, self.tag = store, tag
        self._lock = threading.Lock()
        self._hashes: dict[int, tuple[str, str]] = {}  # fact_id -> (content, its hash)
        self._vectors: dict[int, tuple[tuple, object]] = {}  # fact_id -> ((text hash, size, revision), vector)
        self._matrix: tuple | None = None  # (fresh set, dimension, ids, matrix)
        # The size of the last message's vector: a fact vector of another size (the endpoint changed what it returns
        # under the same name) is made again (pending) instead of staying out of recall for good.
        self._dim: int | None = None

    def _state(self) -> list[tuple[int, str, str, str | None, int, str]]:
        """(fact_id, content, hash of the content, hash its vector of this model was made from or None, the vector's
        byte size, its revision — new with every write, so a vector made again is read again) of every current
        fact."""
        try:
            rows = self.store.read("SELECT f.fact_id, f.content, e.text_hash, LENGTH(e.vector), e.revision FROM facts f "
                                   "LEFT JOIN fact_embeddings e ON e.fact_id = f.fact_id AND e.model = ? "
                                   "ORDER BY f.fact_id", (self.tag,))
        except sqlite3.OperationalError:  # no table yet: a store no writer of 0.14.0 has opened
            return []
        out = []
        for fact_id, content, made_from, size, revision in rows:
            hit = self._hashes.get(fact_id)
            if hit is None or hit[0] != content:
                hit = self._hashes[fact_id] = (content, text_hash(content))
            out.append((fact_id, content, hit[1], made_from, size or 0, revision or ""))
        if len(self._hashes) > len(out) + 1024:  # deleted facts
            alive = {row[0] for row in out}
            self._hashes = {k: v for k, v in self._hashes.items() if k in alive}
        return out

    def ready(self) -> bool:
        """Does any current fact have a vector of this model (worth a call for the message)?"""
        try:
            return bool(self.store.read("SELECT 1 FROM fact_embeddings e JOIN facts f ON f.fact_id = e.fact_id "
                                        "WHERE e.model = ? LIMIT 1", (self.tag,)))
        except sqlite3.OperationalError:
            return False

    def matrix(self, dim: int) -> tuple[list[int], object]:
        """(fact ids, their unit vectors as rows) of the current facts whose vector was made from their present text
        and has *dim* dimensions (the message's)."""
        with self._lock:
            fresh = tuple((fact_id, (digest, size, revision)) for fact_id, _, digest, made_from, size, revision
                          in self._state() if made_from == digest)
            need = [fact_id for fact_id, made in fresh if self._vectors.get(fact_id, ((),))[0] != made]
            for start in range(0, len(need), 500):
                ids = need[start:start + 500]
                for fact_id, digest, blob, revision in self.store.read(
                        f"SELECT fact_id, text_hash, vector, revision FROM fact_embeddings WHERE model = ? AND fact_id "
                        f"IN ({','.join('?' * len(ids))})", (self.tag, *ids)):
                    self._vectors[fact_id] = ((digest, len(blob), revision or ""),
                                              np.frombuffer(bytes(blob), dtype="<f4"))
            if self._matrix is None or self._matrix[0] != fresh or self._matrix[1] != dim:
                alive = {fact_id for fact_id, _ in fresh}
                self._vectors = {k: v for k, v in self._vectors.items() if k in alive}
                made = [(fact_id, self._vectors[fact_id][1]) for fact_id, key in fresh
                        if self._vectors.get(fact_id, ((),))[0] == key]
                rows = [(fact_id, v) for fact_id, v in made if v.shape[0] == dim]
                matrix = np.vstack([v for _, v in rows]) if rows else np.zeros((0, dim), dtype=np.float32)
                self._matrix = (fresh, dim, [fact_id for fact_id, _ in rows], matrix)
            self._dim = dim
            return self._matrix[2], self._matrix[3]

    def _usable(self, digest: str, made_from: str | None, size: int) -> bool:
        """Was this fact's vector made from its present text, and (once a message has been compared) of its size?"""
        return made_from == digest and (self._dim is None or size == 4 * self._dim)

    def pending(self, limit: int, allowed) -> list[tuple[int, str, str]]:
        """Up to *limit* (fact_id, content, hash) of current facts with no usable vector of this model — none, one
        made from an older text, or one of another size than the last message's — newest first; facts *allowed*
        refuses are never sent."""
        with self._lock:
            waiting = [(fact_id, content, digest) for fact_id, content, digest, made_from, size, _ in self._state()
                       if not self._usable(digest, made_from, size)]
        out = []
        for item in reversed(waiting):
            if allowed(item[1]):
                out.append(item)
                if len(out) >= limit:
                    break
        return out

    def counts(self, allowed) -> dict:
        with self._lock:
            state = self._state()
            usable = [self._usable(digest, made_from, size) for _, _, digest, made_from, size, _ in state]
        done = sum(usable)
        held = sum(1 for ok, (_, content, *_rest) in zip(usable, state) if not ok and not allowed(content))
        return {"facts_with_vector": done, "waiting": len(state) - done - held, "never_sent": held}
