from __future__ import annotations

from fastapi import Request


SUPPORTED_LANGS = {"zh", "ja", "en"}
DEFAULT_LANG = "zh"


TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh": {
        "app.name": "Project KB Studio",
        "nav.home": "首页",
        "nav.chat": "Chat",
        "nav.sources": "资料",
        "nav.review": "审核",
        "nav.publish": "发布",
        "nav.agent_hub": "Agent Hub",
        "nav.jobs": "任务",
        "nav.settings": "设置",
        "home.title": "本地项目知识库控制台",
        "home.subtitle": "放资料 → AI 整理 → 人类审核 → 发布给 Agent → 直接对话使用",
        "home.next": "推荐下一步",
        "chat.title": "基于证据的项目问答",
        "chat.evidence_mode": "当前为 Evidence Search Mode，不是完整答案生成。",
        "chat.ask": "提问",
        "chat.placeholder": "输入问题，例如：这个项目有哪些关键风险？",
        "chat.sources": "证据来源",
        "chat.warnings": "提示",
        "chat.no_answer": "当前没有可用 answer engine，仅返回证据片段。",
        "sources.title": "Sources",
        "sources.upload": "上传到 sources/",
        "sources.import": "Import Sources",
        "review.title": "Review",
        "review.approve": "Approve",
        "review.open_obsidian": "Open in Obsidian",
        "publish.title": "Publish",
        "publish.confirm": "Publish Reviewed Docs",
        "agent.title": "Agent Hub",
        "jobs.title": "Jobs",
        "settings.title": "Settings",
        "settings.ui_language": "UI Language",
        "settings.content_language": "Content Language",
        "settings.save": "保存设置",
        "status.local_only": "Local only",
        "action.refresh": "刷新",
        "action.copy": "复制",
        "form.confirm": "确认",
    },
    "ja": {
        "app.name": "Project KB Studio",
        "nav.home": "ホーム",
        "nav.chat": "Chat",
        "nav.sources": "資料",
        "nav.review": "レビュー",
        "nav.publish": "公開",
        "nav.agent_hub": "Agent Hub",
        "nav.jobs": "ジョブ",
        "nav.settings": "設定",
        "home.title": "ローカルプロジェクト知識ベース",
        "home.subtitle": "資料投入 → AI整理 → 人間レビュー → Agent公開 → チャット利用",
        "home.next": "次のおすすめ",
        "chat.title": "証拠ベースのプロジェクトQ&A",
        "chat.evidence_mode": "現在は Evidence Search Mode です。完全な回答生成ではありません。",
        "chat.ask": "質問",
        "chat.placeholder": "質問を入力してください。例：このプロジェクトの主要リスクは？",
        "chat.sources": "証拠ソース",
        "chat.warnings": "注意",
        "chat.no_answer": "利用可能な answer engine がないため、証拠断片のみ返します。",
        "sources.title": "Sources",
        "sources.upload": "sources/ にアップロード",
        "sources.import": "Import Sources",
        "review.title": "Review",
        "review.approve": "承認",
        "review.open_obsidian": "Obsidianで開く",
        "publish.title": "Publish",
        "publish.confirm": "Reviewed Docs を公開",
        "agent.title": "Agent Hub",
        "jobs.title": "Jobs",
        "settings.title": "Settings",
        "settings.ui_language": "UI Language",
        "settings.content_language": "Content Language",
        "settings.save": "設定を保存",
        "status.local_only": "Local only",
        "action.refresh": "更新",
        "action.copy": "コピー",
        "form.confirm": "確認",
    },
    "en": {
        "app.name": "Project KB Studio",
        "nav.home": "Home",
        "nav.chat": "Chat",
        "nav.sources": "Sources",
        "nav.review": "Review",
        "nav.publish": "Publish",
        "nav.agent_hub": "Agent Hub",
        "nav.jobs": "Jobs",
        "nav.settings": "Settings",
        "home.title": "Local Project Knowledge Console",
        "home.subtitle": "Drop files → AI curation → Human review → Publish to agents → Ask directly",
        "home.next": "Recommended Next Step",
        "chat.title": "Evidence-Based Project Q&A",
        "chat.evidence_mode": "Current mode is Evidence Search Mode, not full answer generation.",
        "chat.ask": "Ask",
        "chat.placeholder": "Ask a question, for example: What are the key project risks?",
        "chat.sources": "Evidence Sources",
        "chat.warnings": "Warnings",
        "chat.no_answer": "No answer engine is available, so Studio returns evidence snippets only.",
        "sources.title": "Sources",
        "sources.upload": "Upload to sources/",
        "sources.import": "Import Sources",
        "review.title": "Review",
        "review.approve": "Approve",
        "review.open_obsidian": "Open in Obsidian",
        "publish.title": "Publish",
        "publish.confirm": "Publish Reviewed Docs",
        "agent.title": "Agent Hub",
        "jobs.title": "Jobs",
        "settings.title": "Settings",
        "settings.ui_language": "UI Language",
        "settings.content_language": "Content Language",
        "settings.save": "Save settings",
        "status.local_only": "Local only",
        "action.refresh": "Refresh",
        "action.copy": "Copy",
        "form.confirm": "Confirm",
    },
}


def browser_language(header: str | None) -> str:
    if not header:
        return DEFAULT_LANG
    for part in header.split(","):
        lang = part.split(";")[0].strip().lower().split("-")[0]
        if lang in SUPPORTED_LANGS:
            return lang
    return DEFAULT_LANG


def resolve_lang(request: Request, configured: str | None = None) -> str:
    query_lang = request.query_params.get("lang")
    if query_lang in SUPPORTED_LANGS:
        return query_lang
    if configured and configured != "follow_browser":
        return configured if configured in SUPPORTED_LANGS else DEFAULT_LANG
    cookie_lang = request.cookies.get("lang")
    if cookie_lang in SUPPORTED_LANGS:
        return cookie_lang
    return browser_language(request.headers.get("accept-language"))


def translator(lang: str):
    active = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG

    def t(key: str) -> str:
        return TRANSLATIONS.get(active, TRANSLATIONS[DEFAULT_LANG]).get(key, TRANSLATIONS[DEFAULT_LANG].get(key, key))

    return t
