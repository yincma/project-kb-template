from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .routes import agent_hub, chat, home, jobs, publish, review, settings, sources
from .services.chat_service import ChatService
from .services.command_runner import CommandRunner
from .services.job_runner import JobRunner
from .services.mcp_config import MCPConfigService
from .services.publish import PublishService
from .services.review import ReviewService
from .services.safety import new_token, resolve_project_root, validate_csrf
from .services.state import StateStore
from .services.status import StatusService


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(project_root: str | Path = ".", *, debug: bool = False) -> FastAPI:
    root = resolve_project_root(project_root)
    package_root = Path(__file__).resolve().parent
    app = FastAPI(title="Project KB Studio", debug=debug)

    store = StateStore(root)
    command_runner = CommandRunner(root)
    review_service = ReviewService(root, store)
    publish_service = PublishService(root, store, review_service)

    app.state.project_root = root
    app.state.store = store
    app.state.command_runner = command_runner
    app.state.review_service = review_service
    app.state.publish_service = publish_service
    app.state.job_runner = JobRunner(root, store, command_runner)
    app.state.status_service = StatusService(root, store, review_service, publish_service)
    app.state.chat_service = ChatService(root, store)
    app.state.mcp_service = MCPConfigService(root)
    app.state.csrf_token = new_token()
    app.state.templates = Jinja2Templates(directory=str(package_root / "templates"))

    app.mount("/static", StaticFiles(directory=str(package_root / "static")), name="static")

    @app.middleware("http")
    async def local_security_middleware(request: Request, call_next):
        if request.method in UNSAFE_METHODS:
            try:
                validate_csrf(request, app.state.csrf_token)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    for router in (
        home.router,
        chat.router,
        sources.router,
        review.router,
        publish.router,
        agent_hub.router,
        jobs.router,
        settings.router,
    ):
        app.include_router(router)

    return app


