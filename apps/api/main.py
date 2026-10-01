import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from apps.api.middleware.observability import ObservabilityMiddleware
from apps.api.routes.learning import router as learning_router
from apps.api.routes.learning_loop import router as learning_loop_router
from goalcoach.infrastructure.config import Settings
from goalcoach.infrastructure.llm.pydantic_ai_models import AgentOutputError, LLMUnavailableError
from goalcoach.infrastructure.logging import configure_logging
from goalcoach.infrastructure.persistence.database import (
    create_learner_schema,
    create_session_factory,
    get_engine,
)
from goalcoach.infrastructure.persistence.repositories import (
    ContentRepository,
    SqlAlchemyLearnerRepository,
)

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API with explicitly configured persistence dependencies."""
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved_settings)
        session_factory = create_session_factory(resolved_settings.database_url)
        content_session_factory = create_session_factory(resolved_settings.content_database_url)
        try:
            create_learner_schema(session_factory)
            application.state.learner_repository = SqlAlchemyLearnerRepository(session_factory)
            content_repo = ContentRepository(content_session_factory)
            application.state.content_repository = content_repo
            yield
        finally:
            get_engine(session_factory).dispose()
            get_engine(content_session_factory).dispose()

    application = FastAPI(title="GoalCoach API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(ObservabilityMiddleware)

    @application.exception_handler(LLMUnavailableError)
    async def handle_llm_unavailable(
        _request: Request,
        _exc: LLMUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "LLM unavailable. Check the configured model provider and try again."
            },
        )

    @application.exception_handler(AgentOutputError)
    async def handle_agent_output_error(
        _request: Request,
        exc: AgentOutputError,
    ) -> JSONResponse:
        logger.error("Agent output rejected: %s", exc)
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    # Middleware: Enable CORS for React frontend
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://goalcoachf-app-e5fc7.ondigitalocean.app",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    application.include_router(learning_router)
    application.include_router(learning_loop_router)

    # Health check
    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


def get_learner_repository(request: Request) -> SqlAlchemyLearnerRepository:
    """Resolve the request-scoped learner persistence boundary."""
    from typing import cast

    return cast(SqlAlchemyLearnerRepository, request.app.state.learner_repository)


app = create_app()
