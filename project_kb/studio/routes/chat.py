from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from project_kb.studio.web import render
from project_kb.studio.services.state import utc_now


router = APIRouter()


@router.get("/chat")
async def chat_page(request: Request):
    return render(request, "chat.html")


@router.post("/api/chat/messages")
async def api_chat_message(request: Request):
    body = await request.json()
    question = str(body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")
    response = request.app.state.chat_service.ask(
        question,
        source_mode=body.get("source_mode") or "reviewed",
        search_mode=body.get("search_mode") or "fast",
        provider=body.get("provider") or "local_only",
        content_language=body.get("content_language"),
    )
    request.app.state.store.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, source_refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            __import__("uuid").uuid4().hex,
            None,
            "user",
            question,
            "[]",
            utc_now(),
        ),
    )
    return response
