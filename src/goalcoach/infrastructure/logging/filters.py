"""Logging filters for secret scrubbing, PII redaction, and execution context injection."""

from __future__ import annotations

import logging
import re
from typing import Any

from goalcoach.infrastructure.logging.context import get_context

# Sensitive token detection patterns
_REDACTION_PATTERNS = [
    # API key patterns (OpenAI, OpenRouter, Anthropic, etc.)
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
    # Bearer tokens
    re.compile(r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.]{12,})"),
    # Key-value secret patterns: api_key=..., token=..., password=...
    re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|bearer[_-]?token|secret|password|authorization)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"
    ),
]

REDACTED_SUBSTITUTION = "***REDACTED***"
_SECRET_HINTS = ("sk-", "bearer", "key", "token", "secret", "password", "auth")


def scrub_sensitive_text(text: str) -> str:
    """Scrub known API keys, tokens, and authorization credentials from string text."""
    if not text:
        return text
    text_lower = text.lower()
    if not any(hint in text_lower for hint in _SECRET_HINTS):
        return text
    scrubbed = text
    for pattern in _REDACTION_PATTERNS:
        # If pattern has capturing group, replace the sensitive token portion
        if pattern.groups > 0:

            def _repl(match: re.Match[str]) -> str:
                full = match.group(0)
                captured = match.group(1)
                return full.replace(captured, REDACTED_SUBSTITUTION)

            scrubbed = pattern.sub(_repl, scrubbed)
        else:
            scrubbed = pattern.sub(REDACTED_SUBSTITUTION, scrubbed)
    return scrubbed


def scrub_data(val: Any) -> Any:
    """Recursively scrub data structures (strings, dicts, lists, tuples)."""
    if isinstance(val, str):
        return scrub_sensitive_text(val)
    if isinstance(val, dict):
        scrubbed_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_lower = str(k).lower()
            if any(term in k_lower for term in ("key", "token", "password", "secret", "auth")):
                scrubbed_dict[k] = REDACTED_SUBSTITUTION
            else:
                scrubbed_dict[k] = scrub_data(v)
        return scrubbed_dict
    if isinstance(val, list):
        return [scrub_data(item) for item in val]
    if isinstance(val, tuple):
        return tuple(scrub_data(item) for item in val)
    return val


class SecretScrubbingFilter(logging.Filter):
    """Filter that sanitizes credentials, tokens, and secrets from all log record attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_sensitive_text(record.msg)
        elif record.msg:
            record.msg = scrub_data(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = scrub_data(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    scrub_sensitive_text(a) if isinstance(a, str) else scrub_data(a)
                    for a in record.args
                )

        # Also scrub extra attributes if attached
        if hasattr(record, "extra") and isinstance(record.extra, dict):  # type: ignore[attr-defined]
            record.extra = scrub_data(record.extra)  # type: ignore[attr-defined]

        return True


class ContextFilter(logging.Filter):
    """Filter that injects the active contextvars onto LogRecord attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = get_context()
        record.context = ctx  # type: ignore[attr-defined]
        for key, value in ctx.items():
            setattr(record, key, value)
        return True


__all__ = [
    "REDACTED_SUBSTITUTION",
    "ContextFilter",
    "SecretScrubbingFilter",
    "scrub_data",
    "scrub_sensitive_text",
]
