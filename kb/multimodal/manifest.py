from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any

from kb.multimodal.assets import CaptionResult, VisualAsset, VisualOccurrence
from kb.store import ProjectKBConfig, utc_now_iso


class MultimodalManifest:
    def __init__(self, config: ProjectKBConfig) -> None:
        self.config = config
        self.root = config.multimodal_cache_dir
        self.assets_path = self.root / "assets.jsonl"
        self.occurrences_path = self.root / "occurrences.jsonl"
        self.ingest_runs_path = self.root / "ingest_runs.jsonl"
        self.ocr_cache_dir = self.root / "ocr_cache"
        self.caption_cache_dir = self.root / "caption_cache"

    def load_assets(self) -> dict[str, dict[str, Any]]:
        return _load_jsonl_by_id(self.assets_path, "asset_id")

    def load_occurrences(self) -> dict[str, dict[str, Any]]:
        return _load_jsonl_by_id(self.occurrences_path, "occurrence_id")

    def upsert_asset(self, asset: VisualAsset) -> bool:
        rows = self.load_assets()
        created = asset.asset_id not in rows
        rows[asset.asset_id] = asset.to_json()
        _write_jsonl(self.assets_path, rows.values())
        return created

    def upsert_occurrence(self, occurrence: VisualOccurrence) -> bool:
        rows = self.load_occurrences()
        created = occurrence.occurrence_id not in rows
        rows[occurrence.occurrence_id] = occurrence.to_json()
        _write_jsonl(self.occurrences_path, rows.values())
        return created

    def load_ocr(self, key: str) -> dict[str, Any] | None:
        return _read_json(self.ocr_cache_dir / f"{key}.json")

    def save_ocr(self, key: str, payload: dict[str, Any]) -> None:
        _write_json(self.ocr_cache_dir / f"{key}.json", payload)

    def load_caption(self, key: str) -> dict[str, Any] | None:
        return _read_json(self.caption_cache_dir / f"{key}.json")

    def save_caption(self, result: CaptionResult) -> None:
        if not result.caption_cache_key:
            return
        _write_json(self.caption_cache_dir / f"{result.caption_cache_key}.json", result.to_json())

    def append_ingest_run(self, payload: dict[str, Any]) -> None:
        row = {"indexed_at": utc_now_iso(), **payload}
        self.ingest_runs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ingest_runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def summary(self) -> dict[str, Any]:
        assets = self.load_assets()
        occurrences = self.load_occurrences()
        ocr_files = list(self.ocr_cache_dir.glob("*.json")) if self.ocr_cache_dir.exists() else []
        caption_files = list(self.caption_cache_dir.glob("*.json")) if self.caption_cache_dir.exists() else []
        searchable = 0
        for path in caption_files:
            payload = _read_json(path) or {}
            if payload.get("searchable"):
                searchable += 1
        return {
            "asset_count": len(assets),
            "occurrence_count": len(occurrences),
            "stale_asset_count": sum(1 for row in assets.values() if row.get("stale")),
            "ocr_cache_count": len(ocr_files),
            "caption_cache_count": len(caption_files),
            "expired_ocr_cache_count": 0,
            "expired_caption_cache_count": 0,
            "searchable_visual_chunk_count": searchable,
        }

    def mark_stale_missing_sources(self) -> dict[str, int]:
        assets = self.load_assets()
        occurrences = self.load_occurrences()
        stale_occurrences = 0
        stale_assets: set[str] = set()
        for occurrence_id, occurrence in occurrences.items():
            source_path = occurrence.get("source_path")
            if source_path and not (self.config.root_path / source_path).exists():
                if not occurrence.get("stale"):
                    stale_occurrences += 1
                occurrence["stale"] = True
                if occurrence.get("asset_id"):
                    stale_assets.add(str(occurrence["asset_id"]))
            occurrences[occurrence_id] = occurrence
        for asset_id in stale_assets:
            if asset_id in assets:
                assets[asset_id]["stale"] = True
        if occurrences:
            _write_jsonl(self.occurrences_path, occurrences.values())
        if assets:
            _write_jsonl(self.assets_path, assets.values())
        return {"stale_occurrences": stale_occurrences, "stale_assets": len(stale_assets)}


def _load_jsonl_by_id(path: Path, id_key: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get(id_key):
            rows[str(row[id_key])] = row
    return rows


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in sorted((_asdict(row) for row in rows), key=lambda item: json.dumps(item, sort_keys=True)):
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _asdict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return dict(value)
