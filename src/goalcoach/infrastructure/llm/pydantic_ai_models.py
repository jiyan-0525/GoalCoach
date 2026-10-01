"""
src/goalcoach/infrastructure/llm/pydantic_ai_models.py
PydanticAI model providers for hosted OpenRouter and local Ollama Gemma 4 fallback.
"""

from __future__ import annotations

import logging
from asyncio import sleep
from json import JSONDecodeError
from random import random
from time import perf_counter
from typing import Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunResult

try:
    from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior
except ImportError:
    from pydantic_ai.exceptions import ModelHTTPError as ModelAPIError  # type: ignore[no-redef]
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    ModelHTTPError = ModelAPIError  # type: ignore[misc]

try:
    from pydantic_ai.models.openai import OpenAIChatModel as OpenAIModel
except ImportError:
    from pydantic_ai.models.openai import OpenAIModel  # type: ignore[assignment]
from pydantic_ai.providers.openai import OpenAIProvider

from goalcoach.infrastructure.config import Settings
from goalcoach.infrastructure.telemetry import (
    AgentTelemetryEvent,
    current_request_id,
    emit_agent_telemetry,
    token_usage,
)

logger = logging.getLogger(__name__)


class _TransientRetry:
    """Delay one bounded retry for provider faults that are often self-healing."""

    def __init__(self, max_retries: int) -> None:
        self.remaining = max_retries

    def should_retry(self, err: Exception) -> bool:
        transient = (
            isinstance(err, httpx.TransportError)
            or (
                isinstance(err, ModelHTTPError)
                and err.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
            )
            or isinstance(err, UnexpectedModelBehavior)
        )
        should_retry = transient and self.remaining > 0
        if should_retry:
            self.remaining -= 1
        return should_retry

    @property
    def delay_seconds(self) -> float:
        return 0.5 + random() * 0.5


class LLMUnavailableError(RuntimeError):
    """Raised when no configured LLM can complete an agent request."""


class AgentOutputError(RuntimeError):
    """Raised when a model response cannot satisfy deterministic domain guardrails."""


# Ensure backward compatibility for result.data -> result.output
if not hasattr(AgentRunResult, "data"):
    AgentRunResult.data = property(lambda self: getattr(self, "output", None))  # type: ignore[attr-defined]

# Ensure backward compatibility for Agent(..., result_type=...) -> output_type
_orig_agent_init = Agent.__init__


def _compat_agent_init(self: Any, *args: Any, **kwargs: Any) -> None:
    if "result_type" in kwargs:
        kwargs["output_type"] = kwargs.pop("result_type")
    _orig_agent_init(self, *args, **kwargs)


Agent.__init__ = _compat_agent_init  # type: ignore[method-assign]

try:
    from pydantic_ai.models.test import TestModel

    _orig_test_model_init = TestModel.__init__

    def _compat_test_model_init(self: Any, *args: Any, **kwargs: Any) -> None:
        if "custom_result_text" in kwargs:
            val = kwargs.pop("custom_result_text")
            try:
                import json

                kwargs["custom_output_args"] = json.loads(val)
            except JSONDecodeError:
                kwargs["custom_output_text"] = val
        _orig_test_model_init(self, *args, **kwargs)

    TestModel.__init__ = _compat_test_model_init  # type: ignore[method-assign]
except ImportError:
    pass


def get_openrouter_model() -> OpenAIModel:
    """Returns an OpenAIModel configured for OpenRouter."""
    settings = Settings()
    base_url = str(settings.llm_base_url or "https://openrouter.ai/api/v1")
    api_key = settings.llm_api_key or "unconfigured_key"
    model_name = settings.llm_model or "qwen/qwen-2.5-72b-instruct"
    http_client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
    provider = OpenAIProvider(base_url=base_url, api_key=api_key, http_client=http_client)
    return OpenAIModel(model_name=model_name, provider=provider)


def get_ollama_fallback_model() -> OpenAIModel:
    """Returns an OpenAIModel configured for local Ollama Gemma 4 fallback."""
    settings = Settings()
    base_url = str(settings.fallback_llm_base_url or "http://localhost:11434/v1")
    model_name = settings.fallback_llm_model or "unsloth/gemma-4-12b-it-GGUF"
    http_client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
    provider = OpenAIProvider(base_url=base_url, api_key="ollama", http_client=http_client)
    return OpenAIModel(model_name=model_name, provider=provider)


async def run_with_fallback(
    agent: Any,
    prompt: str,
    deps: Any = None,
    *,
    component: str = "agent",
    prompt_version: str = "v1",
) -> tuple[Any, str]:
    """Run the primary model and optionally use a configured local fallback."""
    settings = Settings()
    if settings.offline_llm_fallback:
        raise LLMUnavailableError("Offline LLM fallback is enabled for deterministic execution")
    primary_model = get_openrouter_model()
    request_id = current_request_id()
    started_at = perf_counter()
    primary_retries = _TransientRetry(settings.llm_max_retries)

    logger.debug(
        "Invoking model inference: component=%s provider=openrouter model=%s prompt_len=%d",
        component,
        primary_model.model_name,
        len(prompt),
        extra={
            "extra": {
                "event": "llm_inference_started",
                "component": component,
                "gen_ai.system": "openrouter",
                "gen_ai.request.model": primary_model.model_name,
                "prompt_length": len(prompt),
            }
        },
    )

    try:
        while True:
            try:
                result = await agent.run(
                    prompt,
                    deps=deps,
                    model=primary_model,
                )
                break
            except (httpx.HTTPError, ModelAPIError, UnexpectedModelBehavior) as err:
                if not primary_retries.should_retry(err):
                    raise
                logger.warning(
                    "Transient primary model %s failure (%s); retrying.",
                    "provider error" if isinstance(err, ModelAPIError) else "transport error",
                    err,
                )
                await sleep(primary_retries.delay_seconds)
        input_tokens, output_tokens = token_usage(result)
        emit_agent_telemetry(
            AgentTelemetryEvent(
                request_id=request_id,
                component=component,
                provider="openrouter",
                model=primary_model.model_name,
                prompt_version=prompt_version,
                latency_ms=round((perf_counter() - started_at) * 1000, 2),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=None,
                validation_succeeded=True,
                fallback_used=False,
            )
        )
        return result, f"openrouter:{primary_model.model_name}"
    except (httpx.HTTPError, ModelAPIError, UnexpectedModelBehavior) as err:
        failure_kind = (
            "output validation"
            if isinstance(err, UnexpectedModelBehavior)
            else "connection or provider"
        )
        if not settings.enable_ollama_fallback:
            emit_agent_telemetry(
                AgentTelemetryEvent(
                    request_id=request_id,
                    component=component,
                    provider="openrouter",
                    model=primary_model.model_name,
                    prompt_version=prompt_version,
                    latency_ms=round((perf_counter() - started_at) * 1000, 2),
                    input_tokens=None,
                    output_tokens=None,
                    estimated_cost_usd=None,
                    validation_succeeded=False,
                    fallback_used=False,
                    failure_kind=failure_kind,
                )
            )
            logger.warning(
                "Primary model %s failure (%s); Ollama fallback is disabled.",
                failure_kind,
                err,
            )
            raise LLMUnavailableError("LLM unavailable: primary model request failed") from err

        logger.warning(
            "Primary model %s failure (%s); trying configured Ollama fallback.",
            failure_kind,
            err,
            extra={
                "extra": {
                    "event": "llm_fallback_engaged",
                    "primary_provider": "openrouter",
                    "primary_model": primary_model.model_name,
                    "failure_kind": failure_kind,
                    "fallback_provider": "ollama",
                }
            },
        )
        fallback_model = get_ollama_fallback_model()
        fallback_started_at = perf_counter()
        fallback_retries = _TransientRetry(min(settings.llm_max_retries, 1))
        try:
            while True:
                try:
                    result = await agent.run(
                        prompt,
                        deps=deps,
                        model=fallback_model,
                    )
                    break
                except (
                    httpx.HTTPError,
                    ModelAPIError,
                    UnexpectedModelBehavior,
                ) as fallback_attempt_err:
                    if not fallback_retries.should_retry(fallback_attempt_err):
                        raise
                    await sleep(fallback_retries.delay_seconds)
            input_tokens, output_tokens = token_usage(result)
            emit_agent_telemetry(
                AgentTelemetryEvent(
                    request_id=request_id,
                    component=component,
                    provider="ollama",
                    model=fallback_model.model_name,
                    prompt_version=prompt_version,
                    latency_ms=round((perf_counter() - fallback_started_at) * 1000, 2),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost_usd=None,
                    validation_succeeded=True,
                    fallback_used=True,
                )
            )
            return result, f"ollama:{fallback_model.model_name}"
        except (httpx.HTTPError, ModelAPIError, UnexpectedModelBehavior) as fallback_err:
            emit_agent_telemetry(
                AgentTelemetryEvent(
                    request_id=request_id,
                    component=component,
                    provider="ollama",
                    model=fallback_model.model_name,
                    prompt_version=prompt_version,
                    latency_ms=round((perf_counter() - fallback_started_at) * 1000, 2),
                    input_tokens=None,
                    output_tokens=None,
                    estimated_cost_usd=None,
                    validation_succeeded=False,
                    fallback_used=True,
                    failure_kind=(
                        "output validation"
                        if isinstance(fallback_err, UnexpectedModelBehavior)
                        else "connection or provider"
                    ),
                )
            )
            raise LLMUnavailableError(
                "LLM unavailable: primary and fallback model requests failed"
            ) from fallback_err


def get_output_retries() -> int:
    """Return the configured number of structured-output validation retries."""
    return Settings().llm_max_retries
