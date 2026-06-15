from pathlib import Path

import yaml

from kb.ingest import discover_files
from kb.store import ProjectKBConfig, load_config


ROOT = Path(__file__).resolve().parents[1]

SOURCE_DIRS = [
    "sources/10_client_inputs",
    "sources/20_our_capabilities",
    "sources/30_case_studies",
    "sources/40_delivery_assets",
    "sources/50_commercial",
    "sources/60_compliance",
]

DOC_DIRS = [
    "docs/01_Maps",
    "docs/10_Clients",
    "docs/20_Requirements",
    "docs/30_Capabilities",
    "docs/40_Case_Studies",
    "docs/50_Delivery",
    "docs/60_Methodology",
    "docs/70_Commercial_Risk",
    "docs/90_Proposal_Blocks",
    "docs/99_Inbox",
    "docs/_templates",
]

TEMPLATES = [
    "client_note_template.md",
    "requirement_matrix_template.md",
    "capability_card_template.md",
    "case_card_template.md",
    "delivery_card_template.md",
    "proposal_block_template.md",
    "moc_template.md",
    "source_note_template.md",
]

REQUIRED_SOURCE_REF_KEYS = {
    "source_path",
    "heading",
    "chunk_index",
    "page_number",
    "slide_number",
    "sheet_name",
    "cell_range",
}


def test_obsidian_curator_structure_exists():
    for rel_path in SOURCE_DIRS + DOC_DIRS:
        assert (ROOT / rel_path).is_dir()

    assert (ROOT / "docs" / "00_HOME.md").is_file()
    assert (ROOT / "obsidian_curator" / "README.md").is_file()
    assert (ROOT / "obsidian_curator" / "SKILL.md").is_file()
    assert len(list((ROOT / "obsidian_curator" / "prompts").glob("*.md"))) == 9
    assert len(list((ROOT / "obsidian_curator" / "examples").glob("*.md"))) == 4


def test_templates_have_required_frontmatter_and_structured_source_refs():
    for template_name in TEMPLATES:
        frontmatter = _frontmatter(ROOT / "docs" / "_templates" / template_name)

        for key in ("type", "status", "created_by", "source_status", "source_refs", "last_reviewed"):
            assert key in frontmatter
        assert frontmatter["status"] == "needs_review"
        assert isinstance(frontmatter["source_refs"], list)
        assert REQUIRED_SOURCE_REF_KEYS.issubset(frontmatter["source_refs"][0])


def test_raw_config_targets_sources_and_raw_cache():
    cfg = load_config(ROOT / "kb" / "config.raw.yaml")

    assert cfg.database.db_path == ".lancedb_raw"
    assert cfg.database.table_name == "project_kb_raw"
    assert cfg.database.manifest_path == ".lancedb_raw/manifest.json"
    assert cfg.database.extracted_cache_dir == ".kb_cache_raw/extracted"
    assert cfg.scan.source_dirs == ["sources"]
    assert cfg.db_path == ROOT / ".lancedb_raw"
    assert cfg.extracted_cache_dir == ROOT / ".kb_cache_raw" / "extracted"


def test_curated_config_targets_docs_and_excludes_non_curated_paths(tmp_path: Path):
    data = yaml.safe_load((ROOT / "kb" / "config.yaml").read_text(encoding="utf-8"))
    scan = data["scan"]

    assert scan["source_dirs"] == ["docs"]
    assert scan["include_patterns"] == ["**/*.md", "**/*.csv", "**/*.yaml", "**/*.yml"]
    for pattern in (
        "docs/.obsidian/**",
        "docs/_attachments/**",
        "docs/_templates/**",
        "docs/99_Inbox/**",
    ):
        assert pattern in scan["exclude_patterns"]

    _write(tmp_path / "docs" / "10_Clients" / "client.md", "# Client\n")
    _write(tmp_path / "docs" / ".obsidian" / "workspace.md", "# Workspace\n")
    _write(tmp_path / "docs" / "_templates" / "template.md", "# Template\n")
    _write(tmp_path / "docs" / "99_Inbox" / "draft.md", "# Draft\n")
    _write(tmp_path / "docs" / "_attachments" / "asset.md", "# Asset\n")

    cfg = ProjectKBConfig(project_root=str(tmp_path))
    cfg.scan.source_dirs = scan["source_dirs"]
    cfg.scan.include_patterns = scan["include_patterns"]
    cfg.scan.exclude_patterns = scan["exclude_patterns"]

    assert [path.relative_to(tmp_path).as_posix() for path in discover_files(cfg)] == [
        "docs/10_Clients/client.md"
    ]


def test_gitignore_excludes_raw_index_cache():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".lancedb_raw/" in text
    assert ".kb_cache_raw/" in text


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, yaml_text, _ = text.split("---", 2)
    return yaml.safe_load(yaml_text)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
