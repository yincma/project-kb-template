from __future__ import annotations

from fastapi import APIRouter, Request

from project_kb.studio.web import render


router = APIRouter()


@router.get("/")
async def home_page(request: Request):
    status = request.app.state.status_service.project_status()
    return render(request, "home.html", status=status)


@router.get("/api/status")
async def api_status(request: Request):
    return request.app.state.status_service.project_status()
