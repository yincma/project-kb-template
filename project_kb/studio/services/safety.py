from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import secrets

from fastapi import HTTPException, Request


SAFE_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def resolve_project_root(project_root: str | Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Project root does not exist: {root}")
    return root


def ensure_inside(child: Path, parent: Path) -> Path:
    resolved_child = child.resolve(strict=False)
    resolved_parent = parent.resolve(strict=False)
    if not resolved_child.is_relative_to(resolved_parent):
        raise ValueError(f"Path escapes allowed root: {child}")
    return resolved_child


def validate_csrf(request: Request, expected_token: str) -> None:
    token = request.headers.get("x-csrf-token")
    if not token or token != expected_token:
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if origin and not _same_origin(origin, request):
        raise HTTPException(status_code=403, detail="Origin is not allowed")
    if referer and not _same_origin(referer, request):
        raise HTTPException(status_code=403, detail="Referer is not allowed")


def _same_origin(value: str, request: Request) -> bool:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return False
    request_url = request.url
    return parsed.scheme == request_url.scheme and parsed.netloc == request_url.netloc

