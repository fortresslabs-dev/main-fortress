"""Structured Logging"""
import logging
import re
import sys
from typing import Any

import structlog

# ── H13: secret redaction ────────────────────────────────────────────
# Patterns that match secrets we know can leak via str(exception) or
# httpx debug output. Each pattern captures the secret-bearing token
# (e.g. "Bearer xxx") and we replace the secret portion with [REDACTED].
#
# Add new patterns here when integrating with a new credentialed
# service. Tuple order: (compiled regex, replacement with \1 prefix).
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # GitHub Personal Access Tokens (classic + fine-grained)
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "ghp_[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github_pat_[REDACTED]"),
    # GitHub legacy 40-char hex token after "token " (no longer issued
    # but old creds may still exist). Anchor on the literal prefix so we
    # do not eat unrelated hex blobs.
    (re.compile(r"\btoken\s+[A-Za-z0-9_\-]{20,}"), "token [REDACTED]"),
    # Authorization headers in any form: "Authorization: <secret>" or
    # "Authorization: <scheme> <secret>". The non-greedy alternation
    # consumes the scheme+secret pair when present, single-token when not.
    (
        re.compile(
            r"(Authorization\s*[:=]\s*)(?:\S+\s+\S+|\S+)",
            flags=re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
    # Bearer tokens (Cognito, JWT, dev tokens)
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"), "Bearer [REDACTED]"),
)


def redact_text(s: str) -> str:
    """Strip known secret patterns from a free-form string.

    Use before passing exception messages, response bodies, or any
    other untrusted text to a logger. Idempotent: re-running on
    already-redacted text is a no-op.
    """
    if not isinstance(s, str) or not s:
        return s
    for pattern, replacement in _REDACT_PATTERNS:
        s = pattern.sub(replacement, s)
    return s


def _redact_processor(_logger, _method_name, event_dict: dict) -> dict:
    """Structlog processor that redacts secrets from every event_dict value.

    Walks values recursively so a token nested inside a dict (e.g. an
    httpx request repr we logged as kwarg) still gets scrubbed.
    """
    def _scrub(value: Any) -> Any:
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, dict):
            return {k: _scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            scrubbed = [_scrub(v) for v in value]
            return type(value)(scrubbed)
        return value

    return {k: _scrub(v) for k, v in event_dict.items()}


def setup_logging():
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str = None):
    return structlog.get_logger(name)
