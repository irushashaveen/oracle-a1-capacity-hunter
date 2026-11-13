"""FastAPI dashboard and local control endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.notifier import Notifier, NotificationError
from app.provisioner import Provisioner
from app.state import AppState

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(state: AppState, provisioner: Provisioner, notifier: Notifier) -> FastAPI:
    worker: Thread | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal worker
        worker = Thread(target=provisioner.run, name="oci-provisioner", daemon=True)
        worker.start()
        try:
            yield
        finally:
            state.request_shutdown()
            if worker is not None:
                worker.join(timeout=10)

    app = FastAPI(
        title="OCI A1 Capacity Hunter",
        description="Local status dashboard for OCI A1 instance provisioning.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Any:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={"title": "OCI A1 Capacity Hunter"},
        )

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return state.snapshot()

    @app.post("/api/toggle")
    def toggle() -> dict[str, bool]:
        return {"running": state.toggle_running()}

    @app.post("/api/test-alert")
    def test_alert() -> dict[str, Any]:
        try:
            channels = notifier.send_test()
        except NotificationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "channels": channels}

    return app
