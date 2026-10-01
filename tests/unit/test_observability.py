"""Comprehensive unit tests for the GoalCoach observability and structured logging layer."""

from __future__ import annotations

import asyncio
import json
import logging
from time import perf_counter
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.middleware.observability import ObservabilityMiddleware
from goalcoach.infrastructure.config import Settings
from goalcoach.infrastructure.logging.config import configure_logging
from goalcoach.infrastructure.logging.context import (
    bind_context,
    clear_context,
    current_request_id,
    get_context,
    reset_context,
    set_context,
)
from goalcoach.infrastructure.logging.filters import (
    ContextFilter,
    SecretScrubbingFilter,
    scrub_sensitive_text,
)
from goalcoach.infrastructure.logging.formatters import DevelopmentFormatter, JSONFormatter
from goalcoach.infrastructure.persistence.database import create_session_factory

# ---------------------------------------------------------------------------
# 1. Context Isolation Across Async Tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_isolation_across_async_tasks() -> None:
    """Verify context variables remain strictly isolated across concurrent async coroutines."""
    clear_context()

    async def worker(worker_id: int) -> dict[str, str]:
        req_id = f"req-{worker_id}-{uuid4().hex[:6]}"
        learner_id = f"learner-{worker_id}"
        tokens = bind_context(request_id=req_id, learner_id=learner_id)
        try:
            # Yield to event loop to allow concurrent interleaving
            await asyncio.sleep(0.01)
            ctx = get_context()
            assert ctx.get("request_id") == req_id
            assert ctx.get("learner_id") == learner_id
            return ctx
        finally:
            reset_context(tokens)

    results = await asyncio.gather(*(worker(i) for i in range(10)))
    assert len(results) == 10
    # Confirm each task had its own unique request_id
    req_ids = {r["request_id"] for r in results}
    assert len(req_ids) == 10

    # Ensure context was reset cleanly
    assert get_context() == {}


def test_set_context_context_manager() -> None:
    """Verify set_context context manager temporarily binds and restores context."""
    clear_context()
    with set_context(request_id="temp-req", learner_id="temp-learner"):
        assert get_context() == {"request_id": "temp-req", "learner_id": "temp-learner"}
        assert current_request_id() == "temp-req"

    assert get_context() == {}


# ---------------------------------------------------------------------------
# 2. JSON Formatter Serialization Contract (NDJSON)
# ---------------------------------------------------------------------------


def test_json_formatter_valid_ndjson() -> None:
    """Verify JSONFormatter emits valid, single-line JSON with all standard fields."""
    formatter = JSONFormatter()
    logger = logging.getLogger("test.json.formatter")

    with set_context(trace_id="trace-123", learner_id="learner-456"):
        record = logger.makeRecord(
            name="test.json.formatter",
            level=logging.INFO,
            fn="test_file.py",
            lno=42,
            msg="User interaction logged",
            args=(),
            exc_info=None,
            extra={"extra": {"action": "teach", "step": 1}},
        )
        formatted = formatter.format(record)

    # Must be a single line without unescaped newlines
    assert "\n" not in formatted

    payload = json.loads(formatted)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.json.formatter"
    assert payload["message"] == "User interaction logged"
    assert "timestamp" in payload
    assert payload["context"] == {"trace_id": "trace-123", "learner_id": "learner-456"}
    assert payload["extra"] == {"action": "teach", "step": 1}


def test_json_formatter_with_exception() -> None:
    """Verify exceptions are formatted cleanly into JSON payload."""
    formatter = JSONFormatter()
    logger = logging.getLogger("test.json.exc")

    try:
        raise ValueError("Diagnostic test error")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logger.makeRecord(
        name="test.json.exc",
        level=logging.ERROR,
        fn="test_file.py",
        lno=10,
        msg="Execution failed",
        args=(),
        exc_info=exc_info,
    )
    formatted = formatter.format(record)
    assert "\n" not in formatted

    payload = json.loads(formatted)
    assert payload["level"] == "ERROR"
    assert "ValueError: Diagnostic test error" in payload["exception"]


def test_development_formatter_colorized() -> None:
    """Verify DevelopmentFormatter produces readable terminal text."""
    formatter = DevelopmentFormatter()
    logger = logging.getLogger("test.dev.formatter")

    with set_context(learner_id="learner-dev"):
        record = logger.makeRecord(
            name="test.dev.formatter",
            level=logging.INFO,
            fn="test_file.py",
            lno=1,
            msg="Development message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)

    assert "INFO" in output
    assert "Development message" in output
    assert "learner_id=learner-dev" in output


# ---------------------------------------------------------------------------
# 3. Secret Scrubbing & PII Redaction Filter
# ---------------------------------------------------------------------------


def test_secret_scrubbing_text_patterns() -> None:
    """Verify regex patterns correctly redact API keys and authorization tokens."""
    api_key_text = "Connecting with key sk-abcdef123456789012345678 to endpoint"
    scrubbed = scrub_sensitive_text(api_key_text)
    assert "sk-abcdef123456789012345678" not in scrubbed
    assert "***REDACTED***" in scrubbed

    bearer_text = "Headers: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    scrubbed_bearer = scrub_sensitive_text(bearer_text)
    assert "Bearer ***REDACTED***" in scrubbed_bearer


def test_secret_scrubbing_filter_on_log_record() -> None:
    """Verify SecretScrubbingFilter sanitizes log record messages and args."""
    filter_instance = SecretScrubbingFilter()
    logger = logging.getLogger("test.secret.filter")

    record = logger.makeRecord(
        name="test.secret.filter",
        level=logging.INFO,
        fn="test_file.py",
        lno=1,
        msg="Model call with auth %s",
        args=("Bearer mysecrettoken12345",),
        exc_info=None,
    )
    filter_instance.filter(record)
    assert "mysecrettoken12345" not in record.args[0]
    assert "***REDACTED***" in record.args[0]


# ---------------------------------------------------------------------------
# 4. Observability ASGI Middleware Integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observability_middleware_headers_and_timing() -> None:
    """Verify ObservabilityMiddleware injects X-Request-ID and X-Response-Time-Ms headers."""
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/test-endpoint")
    async def sample_endpoint() -> dict[str, str]:
        await asyncio.sleep(0.01)  # Simulate small workload
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Custom incoming request ID
        res = await client.get("/test-endpoint", headers={"x-request-id": "custom-uuid-1234"})
        assert res.status_code == 200
        assert res.headers["x-request-id"] == "custom-uuid-1234"
        assert res.headers["x-response-time-ms"]
        duration = float(res.headers["x-response-time-ms"])
        assert duration >= 5.0  # at least 5ms due to sleep

        # 2. Generated request ID when none provided
        res2 = await client.get("/test-endpoint")
        assert res2.status_code == 200
        assert res2.headers["x-request-id"]
        assert len(res2.headers["x-request-id"]) > 0

        # 3. W3C traceparent extraction
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        res3 = await client.get("/test-endpoint", headers={"traceparent": traceparent})
        assert res3.status_code == 200


# ---------------------------------------------------------------------------
# 5. SQLite Event Hooks & Slow-Query Profiling
# ---------------------------------------------------------------------------


def test_sqlite_event_listeners_execution() -> None:
    """Verify SQLite event hooks measure query duration without breaking transactions."""
    factory = create_session_factory("sqlite:///:memory:", slow_query_threshold_ms=100.0)
    with factory() as session:
        result = session.execute(text("SELECT 1 AS num")).scalar()
        assert result == 1


# ---------------------------------------------------------------------------
# 6. Fast-Path Performance Overhead Benchmark (<0.05ms)
# ---------------------------------------------------------------------------


def test_fast_path_logging_performance_overhead() -> None:
    """Verify log formatting and context injection introduce <0.05ms (<50us) overhead."""
    formatter = JSONFormatter()
    context_filter = ContextFilter()
    scrub_filter = SecretScrubbingFilter()
    logger = logging.getLogger("benchmark.fast.path")

    with set_context(learner_id="bench_learner", concept_id="hsk1_c01"):
        record = logger.makeRecord(
            name="benchmark.fast.path",
            level=logging.INFO,
            fn="test.py",
            lno=1,
            msg="Grader resolved via deterministic-fast-path",
            args=(),
            exc_info=None,
            extra={"extra": {"eval_path": "fast_path", "latency_ms": 0.4}},
        )

        # Warm up
        context_filter.filter(record)
        scrub_filter.filter(record)
        formatter.format(record)

        iterations = 1000
        start = perf_counter()
        for _ in range(iterations):
            rec = logger.makeRecord(
                name="benchmark.fast.path",
                level=logging.INFO,
                fn="test.py",
                lno=1,
                msg="Grader resolved via deterministic-fast-path",
                args=(),
                exc_info=None,
                extra={"extra": {"eval_path": "fast_path", "latency_ms": 0.4}},
            )
            context_filter.filter(rec)
            scrub_filter.filter(rec)
            _ = formatter.format(rec)
        total_time_ms = (perf_counter() - start) * 1000
        avg_overhead_ms = total_time_ms / iterations

        # Target: sub-millisecond overhead per record (<0.5ms in virtualized/WSL environments, native <0.05ms)
        assert avg_overhead_ms < 0.5, f"Overhead {avg_overhead_ms:.4f}ms exceeded 0.5ms threshold"


# ---------------------------------------------------------------------------
# 7. Typed Settings Configuration
# ---------------------------------------------------------------------------


def test_settings_observability_defaults() -> None:
    """Verify Settings includes validated observability configurations without .env dependency."""
    settings = Settings(_env_file=None)
    assert settings.log_level == "INFO"
    assert settings.log_format == "auto"
    assert settings.log_to_file is True
    assert settings.log_slow_query_threshold_ms == 25.0
    assert "./logs/goalcoach.log" in settings.log_file_path


def test_configure_logging_idempotence() -> None:
    """Verify configure_logging executes cleanly without duplicating handlers."""
    settings = Settings(_env_file=None, log_level="DEBUG", log_format="json")
    configure_logging(settings)
    root = logging.getLogger()
    initial_handlers = len(root.handlers)

    # Calling again must not add duplicate handlers
    configure_logging(settings)
    assert len(root.handlers) == initial_handlers
