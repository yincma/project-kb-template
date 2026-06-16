from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr


DEFAULT_EXCLUDES = [
    ".git/**",
    ".venv/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "__pycache__/**",
    ".lancedb/**",
    ".kb_cache/**",
    ".codex/**",
    "**/.gitkeep",
    "**/.keep",
    "**/.placeholder",
    "**/.DS_Store",
    "**/Thumbs.db",
    "**/desktop.ini",
]

STORE_SCHEMA_VERSION = 3
V2_METADATA_COLUMNS = [
    "source_path",
    "heading",
    "chunk_index",
    "page_number",
    "slide_number",
    "sheet_name",
    "cell_range",
    "ocr_used",
    "metadata_json",
]
V3_VISUAL_COLUMNS = [
    "indexed_source_path",
    "asset_id",
    "occurrence_id",
    "attachment_path",
    "visual_type",
    "image_hash",
    "caption_provider",
    "caption_model",
    "prompt_version",
    "searchable",
    "confidence",
]


class DatabaseConfig(BaseModel):
    db_path: str = ".lancedb"
    table_name: str = "project_kb"
    manifest_path: str = ".lancedb/manifest.json"
    vector_dimension: int = 1024
    extracted_cache_dir: str = ".kb_cache/extracted"
    multimodal_cache_dir: str = ".kb_cache/multimodal"
    index_role: Literal["raw", "curated"] = "curated"


class ScanConfig(BaseModel):
    source_dirs: list[str] = Field(default_factory=lambda: ["docs"])
    include_patterns: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude_patterns: list[str] = Field(default_factory=lambda: DEFAULT_EXCLUDES.copy())


class ChunkingConfig(BaseModel):
    chunk_size: int = 1000
    chunk_overlap: int = 120


class EmbeddingConfig(BaseModel):
    model_name: str = "BAAI/bge-m3"
    batch_size: int = 4
    device: str | None = None
    use_fp16: bool | None = None


class OCRConfig(BaseModel):
    enabled: bool = False
    engine: str = "rapidocr"
    min_text_chars_per_page: int = 30
    max_pages_per_file: int = 300
    image_dpi: int = 180
    languages: list[str] = Field(default_factory=lambda: ["ch", "en"])


class OfficeParsingConfig(BaseModel):
    extract_images: bool = False
    extract_notes: bool = True
    max_sheet_rows: int = 20000
    xlsx_rows_per_section: int = 500


class MultimodalPDFConfig(BaseModel):
    extract_embedded_images: bool = True
    render_pages: Literal["off", "auto", "all", "keyword_only"] = "off"
    render_all_pages: bool | None = None
    render_dpi: int = 180
    max_rendered_pages_per_file: int = 30
    max_visual_assets_per_file: int = 200
    min_page_text_chars: int = 80
    min_drawing_count_for_render: int = 8
    min_image_area_ratio_for_render: float = 0.25


class MultimodalImagesConfig(BaseModel):
    min_image_width: int = 256
    min_image_height: int = 256
    skip_small_icons: bool = True
    skip_near_blank_images: bool = True
    skip_logo_like_images: bool = True
    max_image_pixels: int = 16_000_000
    deduplicate_by_hash: bool = True


class MultimodalVisionConfig(BaseModel):
    enabled: bool = False
    provider: Literal["stub", "ocr_only", "local", "local_vision", "openai_compatible", "azure", "gemini", "cloud_vision"] = "ocr_only"
    model: str | None = None
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    allow_external_vision: bool = False
    cache_by_hash: bool = True
    prompt_version: str = "vision-caption-v1"
    max_caption_chars: int = 4000
    max_ocr_chars: int = 4000


class CuratedAttachmentsConfig(BaseModel):
    mode: Literal["off", "referenced_only"] = "off"
    allowed_roots: list[str] = Field(default_factory=lambda: ["docs/_attachments/kb_assets"])


class MultimodalParsingConfig(BaseModel):
    enabled: bool = False
    attachments_dir: str = "docs/_attachments/kb_assets"
    pdf: MultimodalPDFConfig = Field(default_factory=MultimodalPDFConfig)
    images: MultimodalImagesConfig = Field(default_factory=MultimodalImagesConfig)
    vision: MultimodalVisionConfig = Field(default_factory=MultimodalVisionConfig)
    curated_attachments: CuratedAttachmentsConfig = Field(default_factory=CuratedAttachmentsConfig)


class ParsingConfig(BaseModel):
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    office: OfficeParsingConfig = Field(default_factory=OfficeParsingConfig)
    multimodal: MultimodalParsingConfig = Field(default_factory=MultimodalParsingConfig)


class BoostRule(BaseModel):
    name: str
    query_terms: list[str] = Field(default_factory=list)
    source_globs: list[str] = Field(default_factory=list)
    weight: float = 0.05


class BoostConfig(BaseModel):
    enabled: bool = True
    rules: list[BoostRule] = Field(
        default_factory=lambda: [
            BoostRule(
                name="risk-register",
                query_terms=["risk", "风险", "严重", "severity", "owner"],
                source_globs=["*风险*.csv", "*risk*.csv", "*risk-register*", "*/风险/*"],
                weight=0.12,
            ),
            BoostRule(
                name="meeting-notes",
                query_terms=["会议", "纪要", "standup", "对账", "负责"],
                source_globs=["*会议纪要*", "*standup*", "*meeting*", "*/会议纪要/*"],
                weight=0.08,
            ),
            BoostRule(
                name="standup-reconciliation",
                query_terms=["对账", "reconciliation", "payment status", "支付状态"],
                source_globs=["*standup*", "*daily*"],
                weight=0.10,
            ),
            BoostRule(
                name="milestones",
                query_terms=["milestone", "里程碑", "关键里程碑", "计划", "日期"],
                source_globs=["*milestone*", "*里程碑*", "*/配置/milestones.*"],
                weight=0.12,
            ),
            BoostRule(
                name="code-api",
                query_terms=["api", "endpoint", "route", "接口", "returns"],
                source_globs=["*/代码/*", "*/code/*", "*.py", "*.ts", "*.tsx", "*.js"],
                weight=0.08,
            ),
            BoostRule(
                name="architecture",
                query_terms=["adr", "architecture", "架构", "schema", "table", "事件驱动"],
                source_globs=["*/架构/*", "*/architecture/*", "*.sql", "*architecture*"],
                weight=0.08,
            ),
            BoostRule(
                name="requirements",
                query_terms=["scope", "需求", "范围", "mvp", "out of scope", "不属于"],
                source_globs=["*/需求/*", "*/requirements/*", "*scope*"],
                weight=0.06,
            ),
        ]
    )


class RetrievalConfig(BaseModel):
    mode: str = "hybrid"
    top_k: int = 5
    candidate_k: int = 20
    max_concurrent_queries: int = 1
    reranker: str = "rrf"
    high_precision: bool = False
    reranker_model_name: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_k: int = 40
    fallback_reranker: str = "rrf"
    boosts: BoostConfig = Field(default_factory=BoostConfig)
    max_snippet_chars: int = 320
    max_return_chars: int = 6000


class CurationConfig(BaseModel):
    index_review_statuses: list[str] = Field(default_factory=lambda: ["reviewed", "approved"])
    skip_needs_review: bool = True


class ProjectKBConfig(BaseModel):
    profile: Literal["lite", "balanced", "accurate"] = "balanced"
    project_root: str = "."
    path_base: Literal["config_dir", "project_root", "cwd"] = "config_dir"
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    curation: CurationConfig = Field(default_factory=CurationConfig)

    _config_path: Path | None = PrivateAttr(default=None)
    _base_dir: Path | None = PrivateAttr(default=None)

    @property
    def root_path(self) -> Path:
        root = Path(self.project_root)
        if root.is_absolute():
            return root
        base = self._base_dir or Path.cwd()
        return (base / root).resolve()

    @property
    def db_path(self) -> Path:
        path = Path(self.database.db_path)
        return path if path.is_absolute() else self.root_path / path

    @property
    def manifest_path(self) -> Path:
        path = Path(self.database.manifest_path)
        return path if path.is_absolute() else self.root_path / path

    @property
    def extracted_cache_dir(self) -> Path:
        path = Path(self.database.extracted_cache_dir)
        return path if path.is_absolute() else self.root_path / path

    @property
    def multimodal_cache_dir(self) -> Path:
        path = Path(self.database.multimodal_cache_dir)
        return path if path.is_absolute() else self.root_path / path

    @property
    def multimodal_attachments_dir(self) -> Path:
        path = Path(self.parsing.multimodal.attachments_dir)
        return path if path.is_absolute() else self.root_path / path


def load_config(config_path: str | Path | None = None) -> ProjectKBConfig:
    if config_path is None:
        config_path = os.environ.get("KB_CONFIG", "kb/config.yaml")
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path

    data: dict[str, Any] = {}
    if path.exists():
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    cfg = _validate_config(data)
    cfg._config_path = path.resolve()
    cfg._base_dir = _resolve_base_dir(cfg, path.resolve())

    if os.environ.get("KB_DB_PATH"):
        cfg.database.db_path = os.environ["KB_DB_PATH"]
        cfg.database.manifest_path = str(Path(os.environ["KB_DB_PATH"]) / "manifest.json")
    if os.environ.get("KB_TABLE_NAME"):
        cfg.database.table_name = os.environ["KB_TABLE_NAME"]
    return cfg


def _resolve_base_dir(cfg: ProjectKBConfig, config_path: Path) -> Path:
    if cfg.path_base in {"project_root", "cwd"}:
        return Path.cwd().resolve()

    base_dir = config_path.parent
    if base_dir.name == "kb":
        base_dir = base_dir.parent
    return base_dir


def _validate_config(data: dict[str, Any]) -> ProjectKBConfig:
    if hasattr(ProjectKBConfig, "model_validate"):
        return ProjectKBConfig.model_validate(data)
    return ProjectKBConfig.parse_obj(data)


@dataclass(frozen=True)
class ManifestEntry:
    sha256: str
    modified_time: float
    chunk_count: int
    indexed_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LanceDBStore:
    def __init__(self, config: ProjectKBConfig) -> None:
        self.config = config
        self._db = None

    def connect(self):
        if self._db is None:
            import lancedb

            self.config.db_path.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self.config.db_path))
        return self._db

    def table_exists(self) -> bool:
        db = self.connect()
        return self.config.database.table_name in db.table_names()

    def drop_table(self) -> None:
        db = self.connect()
        if self.table_exists():
            db.drop_table(self.config.database.table_name)

    def open_or_create_table(self):
        db = self.connect()
        if self.table_exists():
            return db.open_table(self.config.database.table_name)

        import pyarrow as pa

        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), self.config.database.vector_dimension)),
                pa.field("indexed_source_path", pa.string()),
                pa.field("source_path", pa.string()),
                pa.field("file_name", pa.string()),
                pa.field("heading", pa.string()),
                pa.field("chunk_index", pa.int32()),
                pa.field("parser_name", pa.string()),
                pa.field("source_format", pa.string()),
                pa.field("page_number", pa.int32()),
                pa.field("slide_number", pa.int32()),
                pa.field("sheet_name", pa.string()),
                pa.field("row_range", pa.string()),
                pa.field("cell_range", pa.string()),
                pa.field("ocr_used", pa.bool_()),
                pa.field("ocr_confidence", pa.float64()),
                pa.field("extraction_method", pa.string()),
                pa.field("asset_type", pa.string()),
                pa.field("asset_id", pa.string()),
                pa.field("occurrence_id", pa.string()),
                pa.field("attachment_path", pa.string()),
                pa.field("visual_type", pa.string()),
                pa.field("image_hash", pa.string()),
                pa.field("caption_provider", pa.string()),
                pa.field("caption_model", pa.string()),
                pa.field("prompt_version", pa.string()),
                pa.field("searchable", pa.bool_()),
                pa.field("confidence", pa.float64()),
                pa.field("sha256", pa.string()),
                pa.field("modified_time", pa.float64()),
                pa.field("indexed_at", pa.string()),
                pa.field("metadata_json", pa.string()),
            ]
        )
        return db.create_table(self.config.database.table_name, schema=schema)

    def detect_schema_version(self) -> int:
        if not self.table_exists():
            return STORE_SCHEMA_VERSION
        field_names = self.schema_field_names()
        if set(V3_VISUAL_COLUMNS).issubset(field_names):
            return 3
        if {"parser_name", "source_format", "ocr_used"}.issubset(field_names):
            return 2
        return 1

    def schema_field_names(self) -> set[str]:
        if not self.table_exists():
            return set()
        table = self.open_table()
        schema = getattr(table, "schema", None)
        if callable(schema):
            schema = schema()
        names = getattr(schema, "names", None)
        if names is not None:
            return set(names)
        fields = getattr(schema, "fields", None)
        if fields is not None:
            return {field.name for field in fields}
        try:
            return set(table.to_lance().schema.names)
        except Exception:
            return set()

    def open_table(self):
        if not self.table_exists():
            raise RuntimeError(
                f"LanceDB table `{self.config.database.table_name}` does not exist. "
                "Run `uv run project-kb-ingest --config kb/config.yaml` first."
            )
        return self.connect().open_table(self.config.database.table_name)

    def add_rows(self, rows: list[dict[str, Any]]) -> None:
        table = self.open_or_create_table()
        if rows:
            table.add(rows)

    def delete_sources(self, source_paths: list[str]) -> None:
        if not source_paths or not self.table_exists():
            return
        quoted = ", ".join(_sql_quote(path) for path in source_paths)
        if "indexed_source_path" in self.schema_field_names():
            self.open_table().delete(f"source_path IN ({quoted}) OR indexed_source_path IN ({quoted})")
        else:
            self.open_table().delete(f"source_path IN ({quoted})")

    def ensure_fts_index(self, *, replace: bool = False) -> str | None:
        try:
            table = self.open_or_create_table()
            table.create_fts_index("text", replace=replace)
            return None
        except Exception as exc:
            if not replace and _looks_like_existing_index_error(exc):
                return None
            return f"Could not create LanceDB full-text index: {exc}"

    def count_rows(self) -> int | None:
        if not self.table_exists():
            return 0
        table = self.open_table()
        try:
            return int(table.count_rows())
        except Exception:
            try:
                return len(table.to_lance().to_table())
            except Exception:
                return None

    def preview_rows(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.table_exists():
            return []
        table = self.open_table()
        limit = max(1, int(limit))
        if hasattr(table, "head"):
            try:
                rows = _arrowish_to_rows(table.head(limit))
                if rows:
                    return rows
            except Exception:
                pass
        if hasattr(table, "to_arrow"):
            try:
                rows = _arrowish_to_rows(table.to_arrow())
                return rows[:limit]
            except Exception:
                pass
        try:
            rows = _arrowish_to_rows(table.to_lance().to_table())
            return rows[:limit]
        except Exception:
            return []

    def metadata_summary(self, sample_limit: int = 200) -> dict[str, Any]:
        field_names = self.schema_field_names()
        rows = self.preview_rows(sample_limit)
        required_columns = V2_METADATA_COLUMNS + V3_VISUAL_COLUMNS
        return {
            "required_columns": required_columns,
            "missing_columns": [column for column in required_columns if column not in field_names],
            "sampled_rows": len(rows),
            "source_formats": sorted({str(row.get("source_format")) for row in rows if row.get("source_format")}),
            "parser_names": sorted({str(row.get("parser_name")) for row in rows if row.get("parser_name")}),
            "location_fields_with_values": {
                field: sum(1 for row in rows if row.get(field) not in (None, ""))
                for field in ("page_number", "slide_number", "sheet_name", "cell_range")
            },
            "ocr_used_rows": sum(1 for row in rows if bool(row.get("ocr_used"))),
            "visual_rows": sum(1 for row in rows if row.get("asset_type") == "visual"),
            "searchable_visual_rows": sum(
                1 for row in rows if row.get("asset_type") == "visual" and row.get("searchable") is not False
            ),
        }


def load_manifest(config: ProjectKBConfig) -> dict[str, Any]:
    path = config.manifest_path
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "files": {}}


def save_manifest(config: ProjectKBConfig, manifest: dict[str, Any]) -> None:
    path = config.manifest_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def extracted_cache_path(config: ProjectKBConfig, sha256: str) -> Path:
    return config.extracted_cache_dir / f"{sha256}.txt"


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _looks_like_existing_index_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "exists" in message or "duplicate" in message


def _arrowish_to_rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_pylist"):
        return list(value.to_pylist())
    if hasattr(value, "read_all"):
        return _arrowish_to_rows(value.read_all())
    if hasattr(value, "to_table"):
        return _arrowish_to_rows(value.to_table())
    if isinstance(value, list):
        return [dict(row) for row in value]
    try:
        return [dict(row) for row in value]
    except Exception:
        return []
