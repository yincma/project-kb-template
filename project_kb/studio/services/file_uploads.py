from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Iterable

from fastapi import UploadFile

from .safety import ensure_inside


MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_FILES = 20
CHUNK_SIZE = 1024 * 1024


class UploadRejected(ValueError):
    pass


@dataclass
class UploadedSource:
    original_name: str
    saved_name: str
    rel_path: str
    size: int


class FileUploadService:
    def __init__(self, project_root: Path, *, max_file_bytes: int = MAX_FILE_BYTES, max_files: int = MAX_UPLOAD_FILES) -> None:
        self.project_root = project_root.resolve()
        self.sources_dir = (self.project_root / "sources").resolve(strict=False)
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files

    async def save_uploads(self, uploads: Iterable[UploadFile]) -> list[UploadedSource]:
        upload_list = list(uploads)
        if len(upload_list) > self.max_files:
            raise UploadRejected(f"Too many files. Limit is {self.max_files}.")
        saved: list[UploadedSource] = []
        for upload in upload_list:
            saved.append(await self.save_upload(upload))
        return saved

    async def save_upload(self, upload: UploadFile) -> UploadedSource:
        original = upload.filename or ""
        safe_name = sanitize_filename(original)
        if not self.sources_dir.exists():
            self.sources_dir.mkdir(parents=True, exist_ok=True)
        resolved_sources = self.sources_dir.resolve(strict=True)
        if not resolved_sources.is_relative_to(self.project_root):
            raise UploadRejected("sources/ resolves outside the project root.")
        target = unique_target(resolved_sources, safe_name)
        ensure_inside(target, resolved_sources)

        size = 0
        try:
            with target.open("xb") as handle:
                while True:
                    chunk = await upload.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_file_bytes:
                        raise UploadRejected(f"File is too large. Limit is {self.max_file_bytes} bytes.")
                    handle.write(chunk)
        except Exception:
            if target.exists() and target.is_file():
                target.unlink()
            raise
        finally:
            await upload.close()

        rel_path = target.relative_to(self.project_root).as_posix()
        return UploadedSource(original_name=original, saved_name=target.name, rel_path=rel_path, size=size)


def sanitize_filename(filename: str) -> str:
    name = filename.strip()
    if not name:
        raise UploadRejected("Filename is empty.")
    if "/" in name or "\\" in name:
        raise UploadRejected("Filename must not include path separators.")
    if name in {".", ".."}:
        raise UploadRejected("Filename is invalid.")
    name = re.sub(r"[^\w.\- ()\u3040-\u30ff\u3400-\u9fff]", "_", name, flags=re.UNICODE)
    name = re.sub(r"_+", "_", name).strip(" .")
    if not name:
        raise UploadRejected("Filename is empty after sanitization.")
    return name


def unique_target(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 10_000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise UploadRejected("Could not allocate a unique filename.")


def open_folder(path: Path) -> None:
    if shutil.which("open"):
        import subprocess

        subprocess.run(["open", str(path)], shell=False, check=False)

