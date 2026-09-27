# Keeps API keys out of logs, events, errors and API responses.
# Known secrets (from env, config, or a key passed to validate) are replaced by value; common key shapes
# (Google keys, bearer tokens, ?key= parameters, passwords in database URLs) by pattern.

import logging
import os
import re

SECRET_VARS = ("GEMINI_API_KEY", "TYPESAFE_API_KEY", "OPENROUTER_API_KEY", "CLOUDFLARE_API_TOKEN", "DATABASE_URL")
MASK = "[redacted]"
MIN_LEN = 8  # shorter values would blank out ordinary words
PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), MASK),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1" + MASK),
    (re.compile(r"(?i)([?&](?:key|api_key|apikey|token)=)[^&\s\"']+"), r"\1" + MASK),
    (re.compile(r"(?i)(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+"), r"\1" + MASK),
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), r"\1" + MASK + "@"),
]
_remembered: set[str] = set()


def remember(secret: str | None) -> None:
    # A key that isn't in the environment (typed into a setup screen) is redacted too.
    if secret and len(secret.strip()) >= MIN_LEN:
        _remembered.add(secret.strip())


def secrets() -> list[str]:
    from app import config  # config never imports this module, so no cycle

    found = {os.getenv(v, "").strip() for v in SECRET_VARS} | {config.GEMINI_API_KEY, config.DATABASE_URL} | _remembered
    return sorted((s for s in found if s and len(s) >= MIN_LEN), key=len, reverse=True)


def contains_secret(text: str) -> bool:
    return any(s in text for s in secrets())


def redact(text: object) -> str:
    out = str(text)
    for s in secrets():
        out = out.replace(s, MASK)
    for pattern, repl in PATTERNS:
        out = pattern.sub(repl, out)
    return out


_installed = False


def install_logging() -> None:
    # Every log record, from any logger (httpx, the Gemini SDK, ours), is redacted when it's created,
    # tracebacks included.
    global _installed
    if _installed:
        return
    _installed = True
    base = logging.getLogRecordFactory()
    formatter = logging.Formatter()

    def clean(v: object) -> object:
        return redact(v) if isinstance(v, str) else v

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        try:
            # Keep msg and args in their own shapes: formatters like uvicorn's access log unpack args themselves.
            record.msg = clean(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(clean(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: clean(v) for k, v in record.args.items()}
            if record.args:  # a key inside an exception or object arg only shows once formatted
                text = record.getMessage()
                if (safe := redact(text)) != text:
                    record.msg, record.args = safe, ()
        except Exception:  # a record that can't format keeps its own error path
            return record
        if record.exc_info:
            record.exc_text = redact(formatter.formatException(record.exc_info))
            record.exc_info = None
        return record

    logging.setLogRecordFactory(factory)
