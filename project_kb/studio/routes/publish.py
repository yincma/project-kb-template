from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from project_kb.studio.web import render
from project_kb.studio.services.command_runner import CommandEnum


router = APIRouter()


@router.get("/publish")
async def publish_page(request: Request):
    preview = request.app.state.publish_service.preview()
    return render(request, "publish.html", preview=preview)


@router.get("/api/publish/preview")
async def api_publish_preview(request: Request):
    return request.app.state.publish_service.preview()


@router.post("/api/publish/confirm")
async def api_publish_confirm(request: Request):
    publish_service = request.app.state.publish_service

    def write_publish_report(job_id, result, status):
        publish_service.write_report(
            job_id,
            extra={
                "job_status": status,
                "exit_code": result.exit_code,
                "stdout_preview": result.stdout[-2000:],
                "stderr_preview": result.stderr[-2000:],
            },
        )

    try:
        job_id = request.app.state.job_runner.enqueue_command(
            job_type="publish",
            command=CommandEnum.PUBLISH_REVIEWED_DOCS,
            on_complete=write_publish_report,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    report_path = publish_service.report_path(job_id)
    return {"job_id": job_id, "report_path": report_path.relative_to(request.app.state.project_root).as_posix()}
