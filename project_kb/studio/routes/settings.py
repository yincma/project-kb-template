from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from project_kb.studio.web import current_config_profile, default_settings, render
from project_kb.studio.services.i18n import TRANSLATIONS, browser_language


router = APIRouter()


@router.get("/settings")
async def settings_page(request: Request):
    return render(request, "settings.html")


@router.get("/api/settings")
async def api_get_settings(request: Request):
    return {
        "settings": default_settings(request.app.state.store.get_settings(), request.app.state.project_root),
        "csrf_token": request.app.state.csrf_token,
    }


@router.post("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    _validate_settings(body, current_config_profile(request.app.state.project_root))
    allowed = set(SETTING_ENUMS) | {"external_llm_enabled"}
    for key, value in body.items():
        if key in allowed:
            if key == "profile":
                continue
            request.app.state.store.set_setting(key, value)
    return {"settings": default_settings(request.app.state.store.get_settings(), request.app.state.project_root)}


@router.get("/api/i18n/{lang}")
async def api_i18n(lang: str, request: Request):
    if lang == "follow_browser":
        lang = browser_language(request.headers.get("accept-language"))
    return {"lang": lang if lang in TRANSLATIONS else "zh", "messages": TRANSLATIONS.get(lang, TRANSLATIONS["zh"])}


SETTING_ENUMS = {
    "ui_language": {"zh", "ja", "en", "follow_browser"},
    "content_language": {"zh", "ja", "en"},
    "profile": {"lite", "balanced", "accurate"},
    "default_chat_source": {"reviewed", "raw", "both"},
    "ocr": {"off", "on"},
    "visual_extraction": {"off", "on", "experimental"},
}


def _validate_settings(body: dict, actual_profile: str) -> None:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Settings payload must be an object.")
    for key, value in body.items():
        if key in SETTING_ENUMS and value not in SETTING_ENUMS[key]:
            raise HTTPException(status_code=400, detail=f"Invalid value for {key}.")
        if key == "external_llm_enabled" and not isinstance(value, bool):
            raise HTTPException(status_code=400, detail="external_llm_enabled must be a boolean.")
    requested_profile = body.get("profile")
    if requested_profile is not None and requested_profile != actual_profile:
        raise HTTPException(
            status_code=400,
            detail="Profile is read from kb/config.yaml in this MVP. Use project-kb-profile to change it.",
        )
