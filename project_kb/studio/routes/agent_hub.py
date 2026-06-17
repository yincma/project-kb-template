from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from project_kb.studio.web import render


router = APIRouter()


@router.get("/agent-hub")
async def agent_hub_page(request: Request):
    return render(request, "agent_hub.html", agent_status=request.app.state.mcp_service.status())


@router.get("/api/agent-hub/status")
async def api_agent_hub_status(request: Request):
    return request.app.state.mcp_service.status()


@router.post("/api/agent-hub/{agent}/preview-install")
async def api_preview_install(agent: str, request: Request):
    try:
        return request.app.state.mcp_service.preview_install(agent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/agent-hub/{agent}/confirm-install")
async def api_confirm_install(agent: str, request: Request):
    try:
        return request.app.state.mcp_service.confirm_install(agent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
