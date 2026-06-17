from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from project_kb.studio.web import render


router = APIRouter()


@router.get("/review")
async def review_page(request: Request):
    notes = request.app.state.review_service.scan_notes()
    return render(request, "review.html", notes=notes)


@router.get("/api/review-items")
async def api_review_items(request: Request):
    notes = request.app.state.review_service.scan_notes()
    return {"items": [note.__dict__ for note in notes]}


@router.post("/api/review/{note_id}/approve")
async def api_approve_note(note_id: str, request: Request):
    body = await request.json()
    try:
        note = request.app.state.review_service.approve(
            note_id,
            override_missing_refs=bool(body.get("override_missing_refs", False)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"note": note.__dict__}


@router.post("/api/review/{note_id}/mark-evidence-gap")
async def api_mark_evidence_gap(note_id: str, request: Request):
    try:
        note = request.app.state.review_service.mark_status(note_id, "evidence_gap")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"note": note.__dict__}


@router.post("/api/review/{note_id}/mark-duplicate")
async def api_mark_duplicate(note_id: str, request: Request):
    try:
        note = request.app.state.review_service.mark_status(note_id, "possible_duplicate")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"note": note.__dict__}
