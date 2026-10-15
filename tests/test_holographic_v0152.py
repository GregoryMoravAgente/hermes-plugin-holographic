"""0.15.2: an environment variable that holds a key, a token or a password is a credential with its value (issue #3:
"配置OPENAI_API_KEY=…" was stored and recalled); the popular names of model and service providers are listed."""
from __future__ import annotations

import pytest

from hermes_plugin_holographic import _SECRET_ENV_NAMES, _fact_problem, _recall_problem, _secret_in  # noqa: E402

# A made-up value, put together at run time so that secret scanners do not flag the test file.
VALUE = "a1B2" + "c3D4" + "e5F6" + "g7H8"


def _refused(text):
    return bool(_fact_problem(text) and _recall_problem(text) and _secret_in(text))


def _kept(text):
    return _fact_problem(text) is None and _recall_problem(text) is None


@pytest.mark.parametrize("name", _SECRET_ENV_NAMES)
def test_every_listed_variable_with_a_value_is_refused(name):
    assert _refused(f"{name}={VALUE}")


@pytest.mark.parametrize("text", [
    f"配置OPENAI_API_KEY={VALUE}",  # issue #3: glued to Chinese text
    f"export ANTHROPIC_API_KEY={VALUE}",
    f"GROQ_API_KEY: {VALUE}",
    f"DEEPSEEK_API_KEY = {VALUE}",
    f"openai_api_key = {VALUE}",  # a listed name in lower case
    f'{{"GEMINI_API_KEY": "{VALUE}"}}',
    f"  HF_TOKEN: {VALUE}",
    f"MY_SERVICE_TOKEN={VALUE}",  # not listed, ends like a secret
    f"ACME_CLIENT_SECRET={VALUE}",
    f"ACME_DB_PASSWORD={VALUE}",
    f"TG_API_HASH={'0123456789abcdef' * 2}",  # listed: does not end like a secret
    f"QIANFAN_AK={VALUE}",
    f"Ключ OPENAI_API_KEY={VALUE} лежит в .env",
    f"SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T0/B0/{VALUE}",  # the link is the secret
    f"SENTRY_DSN=https://{VALUE}@o1.ingest.sentry.io/42",
    f"DATABASE_URL=postgres://app:{VALUE}@db:5432/app",  # a password inside an address
    f"see https://admin:{VALUE}@example.com",
])
def test_a_variable_with_its_secret_is_refused(text):
    assert _refused(text)


@pytest.mark.parametrize("text", [
    "MAX_TOKENS=128000",  # a count
    "MAX_OUTPUT_TOKENS=8192",
    "PASSWORD_MIN_LENGTH=12",  # the secret word is not at the end
    "embedding_key_env: OPENAI_API_KEY",  # names the variable
    "OPENAI_API_KEY=${OPENAI_API_KEY}",
    "OPENAI_API_KEY=%OPENAI_API_KEY%",
    "OPENAI_API_KEY=***",  # redacted
    "OPENAI_API_KEY=****",
    "OPENAI_API_KEY=sk-...",  # a placeholder
    "OPENAI_API_KEY=<your key>",
    "OPENAI_API_KEY=[REDACTED]",
    "GITHUB_TOKEN: from_env",
    "GOOGLE_APPLICATION_CREDENTIALS=/home/anton/gcp.json",  # a path
    "OPENAI_API_KEY лежит в ~/.hermes/.env",
    "GEMINI_API_KEY is set on the server",
    "The bot reads TELEGRAM_BOT_TOKEN from .env",
    "Ключ GROQ_API_KEY общий с video-watch",
    "DATABASE_URL=postgres://localhost:5432/app",  # no password in it
    "cache_key: user_42",  # code, not an environment variable
    "sort_key = created_at",
])
def test_a_setting_without_a_secret_is_kept(text):
    assert _kept(text)


@pytest.mark.parametrize("text", ["Лимит tokens (input) у модели 128k", "токенов (вход и выход) хватает"])
def test_prose_in_brackets_after_the_word_is_kept(text):
    assert _kept(text)


def test_a_value_in_brackets_with_digits_stays_a_secret():
    assert _fact_problem("пароль (Qw3rty2024)")
