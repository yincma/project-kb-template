from __future__ import annotations

from fastapi import Request

from .services.i18n import resolve_lang, translator


def render(request: Request, template_name: str, **context):
    payload = template_context(request, **context)
    response = request.app.state.templates.TemplateResponse(request, template_name, payload)
    response.set_cookie("lang", payload["lang"], samesite="strict")
    return response


def template_context(request: Request, **extra):
    store = request.app.state.store
    lang = resolve_lang(request, store.get_setting("ui_language", "follow_browser"))
    t = translator(lang)
    context = {
        "request": request,
        "lang": lang,
        "t": t,
        "csrf_token": request.app.state.csrf_token,
        "settings": default_settings(store.get_settings()),
    }
    context.update(extra)
    return context


def default_settings(settings: dict) -> dict:
    defaults = {
        "ui_language": "follow_browser",
        "content_language": "follow_source",
        "profile": "lite",
        "ocr": "off",
        "visual_extraction": "off",
        "default_chat_source": "reviewed",
        "external_llm_enabled": False,
    }
    return {**defaults, **settings}
