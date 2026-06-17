from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

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
    provider = body.get("provider") or "local_only"
    if provider == "external_llm":
        raise HTTPException(status_code=501, detail="External LLM mode is not implemented in this MVP.")
    response = await run_in_threadpool(
        request.app.state.chat_service.ask,
        question,
        source_mode=body.get("source_mode") or "reviewed",
        search_mode=body.get("search_mode") or "fast",
        provider=provider,
        content_language=body.get("content_language"),
    )
    _save_chat_messages(request, question=question, response=response, provider=provider)
    return response


def _save_chat_messages(request: Request, *, question: str, response: dict, provider: str) -> None:
    store = request.app.state.store
    store.execute(
        """
        INSERT INTO chat_messages
            (id, session_id, role, content, source_refs_json, warnings_json, mode, provider, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            None,
            "user",
            question,
            "[]",
            "[]",
            None,
            provider,
            utc_now(),
        ),
    )
    assistant_content = (
        str(response.get("answer") or "")
        if response.get("answer_available")
        else "Evidence Search Mode: no full answer was generated. Review the evidence panel."
    )
    store.execute(
        """
        INSERT INTO chat_messages
            (id, session_id, role, content, source_refs_json, warnings_json, mode, provider, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            None,
            "assistant",
            assistant_content,
            json.dumps(response.get("source_refs") or [], ensure_ascii=False),
            json.dumps(response.get("warnings") or [], ensure_ascii=False),
            response.get("mode"),
            response.get("requested_provider") or provider,
            utc_now(),
        ),
    )
