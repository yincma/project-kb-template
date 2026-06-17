from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from project_kb.studio.web import render
from project_kb.studio.services.command_runner import CommandEnum
from project_kb.studio.services.file_uploads import FileUploadService, UploadRejected


router = APIRouter()


@router.get("/sources")
async def sources_page(request: Request):
    return render(request, "sources.html", sources=request.app.state.status_service.sources())


@router.get("/api/sources")
async def api_sources(request: Request):
    return {"sources": request.app.state.status_service.sources()}


@router.post("/api/sources/upload")
async def api_upload_sources(request: Request, files: list[UploadFile] = File(...)):
    service = FileUploadService(request.app.state.project_root)
    try:
        saved = await service.save_uploads(files)
    except UploadRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for item in saved:
        request.app.state.store.upsert_file(rel_path=item.rel_path, size=item.size, mtime=0, status="New")
    return {"uploaded": [item.__dict__ for item in saved]}


@router.post("/api/jobs/import")
async def api_import_sources(request: Request):
    try:
        job_id = request.app.state.job_runner.enqueue_command(job_type="import", command=CommandEnum.IMPORT_SOURCES)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "queued"}


@router.post("/api/jobs/curate")
async def api_curate_notes(request: Request):
    try:
        job_id = request.app.state.job_runner.enqueue_command(job_type="curate", command=CommandEnum.CURATE_NOTES)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "queued", "capability": "not_implemented"}
