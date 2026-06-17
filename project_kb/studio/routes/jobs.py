from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from project_kb.studio.web import render


router = APIRouter()


@router.get("/jobs")
async def jobs_page(request: Request):
    jobs = request.app.state.store.query_all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100")
    return render(request, "jobs.html", jobs=jobs)


@router.get("/api/jobs")
async def api_jobs(request: Request):
    return {"jobs": request.app.state.store.query_all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100")}


@router.get("/api/jobs/{job_id}")
async def api_job_detail(job_id: str, request: Request):
    job = request.app.state.store.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"job": job}


@router.get("/api/jobs/{job_id}/logs")
async def api_job_logs(job_id: str, request: Request):
    logs = request.app.state.store.query_all("SELECT * FROM job_logs WHERE job_id = ? ORDER BY created_at", (job_id,))
    job_dir = request.app.state.store.jobs_dir / job_id
    files = {}
    for name in ("stdout.log", "stderr.log"):
        path = job_dir / name
        files[name] = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return {"logs": logs, "files": files}
