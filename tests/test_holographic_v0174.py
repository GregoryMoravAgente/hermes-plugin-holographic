"""0.17.4: three more texts the credential screen read as a credential with its value (issue #6 by vlat0101, second
round), and "1 fact" in the search by meaning's report (issue #5 by sprd-tmu)."""
from __future__ import annotations

import pytest

from hermes_plugin_holographic import _fact_problem, _has_credential, _recall_problem, _sendable, embeddings

# The issue's table: a verb "pins", a bracket that closes before "=", a folder; and the same shapes around them
NAMES = [
    "the report module pins LEDGER = _PROD_LEDGER and ignores TOKEN_SAVER_STATS",
    "- $60k for 22-79k input tokens) = token-accounting bug",
    "~/.hermes/token-saver-ccr/",
    "the build pins numpy = 1.26 for now", "Pins LEDGER = _PROD_LEDGER in the test",
    "(input tokens): token-accounting", "(password): correct-horse",
    "token-saver/cache/", "plugins/token-saver-ccr/ holds the cache", "C:\\hermes\\token-saver\\cache\\",
    "see ~/.hermes/token-saver-ccr/ for the cache", "token-abc/",
]

# What stays a secret: a PIN with what it is for, a bracket that is part of the name, a folder shaped like a password,
# and the case the issue asked to keep
SECRETS = [
    "password Summer2026.txt", "PIN card: 4821", "PINs card: 4821", "pins: 4821", "the PINs = 4821, 9932",
    "pins-wifi: 4821", "pins (prod) = 4821",
    "Home wifi (password): Qwerty123", "wifi (password) = correct-horse-battery", "password(wifi): Qwerty123",
    "token (prod) = sk9Lm2Qw8x", "api key(prod): Abc123xyz", "tokens)=Qwerty123", "tokens): Qwerty123!",
    "Password wifi: correct-horse", "password wifi: Qwerty123",
    "token-Secret2026/", "password-P@ss/",
]


@pytest.mark.parametrize("text", NAMES)
def test_prose_and_a_folder_are_no_secret(text):
    assert not _has_credential(text)
    assert _fact_problem(text) is None and _recall_problem(text) is None


@pytest.mark.parametrize("text", SECRETS)
def test_a_secret_stays_one(text):
    assert _has_credential(text)
    assert _fact_problem(text) and _recall_problem(text) and not _sendable(text)


@pytest.mark.parametrize("count, said", [(1, "1 fact"), (0, "0 facts"), (2, "2 facts"), (12, "12 facts")])
def test_one_fact_is_one_fact(count, said):
    assert embeddings._facts(count) == said
