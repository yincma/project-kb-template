from __future__ import annotations

from fastapi import Request

from kb.store import load_config

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
        "settings": default_settings(store.get_settings(), request.app.state.project_root),
    }
    context.update(extra)
    return context


def default_settings(settings: dict, project_root=None) -> dict:
    defaults = {
        "ui_language": "follow_browser",
        "content_language": "follow_source",
        "profile": "balanced",
        "ocr": "off",
        "visual_extraction": "off",
        "default_chat_source": "reviewed",
        "external_llm_enabled": False,
    }
    merged = {**defaults, **settings}
    merged["profile"] = current_config_profile(project_root)
    return merged


def current_config_profile(project_root) -> str:
    if project_root is None:
        return "balanced"
    try:
        return load_config(project_root / "kb" / "config.yaml").profile
    except Exception:
        return "balanced"
