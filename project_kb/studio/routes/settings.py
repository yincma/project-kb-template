from __future__ import annotations

from fastapi import APIRouter, Request

from project_kb.studio.web import default_settings, render
from project_kb.studio.services.i18n import TRANSLATIONS, browser_language


router = APIRouter()


@router.get("/settings")
async def settings_page(request: Request):
    return render(request, "settings.html")


@router.get("/api/settings")
async def api_get_settings(request: Request):
    return {"settings": default_settings(request.app.state.store.get_settings()), "csrf_token": request.app.state.csrf_token}


@router.post("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    allowed = {
        "ui_language",
        "content_language",
        "profile",
        "ocr",
        "visual_extraction",
        "default_chat_source",
        "external_llm_enabled",
    }
    for key, value in body.items():
        if key in allowed:
            request.app.state.store.set_setting(key, value)
    return {"settings": default_settings(request.app.state.store.get_settings())}


@router.get("/api/i18n/{lang}")
async def api_i18n(lang: str, request: Request):
    if lang == "follow_browser":
        lang = browser_language(request.headers.get("accept-language"))
    return {"lang": lang if lang in TRANSLATIONS else "zh", "messages": TRANSLATIONS.get(lang, TRANSLATIONS["zh"])}
