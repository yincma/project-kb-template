# Project KB 本地项目知识库模板

复制这个模板到任意项目根目录，把资料放进 `docs/`，重建索引后即可用命令行、Codex MCP 或 Kiro MCP 查询项目资料。

## 目录结构

```text
project-root/
├── docs/
├── kb/
├── .codex/config.toml
├── .kiro/settings/mcp.json
├── .kiro/steering/project-kb.md
├── AGENTS.md
├── README.md
├── pyproject.toml
├── uv.lock
├── .env.example
└── .gitignore
```

## 安装

```bash
uv sync --extra ocr
```

首次运行会下载本地 embedding/reranker 模型。下载完成后，索引和查询都在本地运行。

## 放入资料

把资料放到：

```text
docs/
```

支持格式：

```text
pdf, pptx, xlsx, docx, md, txt, csv, json, yaml, yml, py, ts, tsx, js, sql, toml, ini
```

如需改资料目录，编辑 `kb/config.yaml` 的 `scan.source_dirs`。

## 建立索引

首次或全量刷新：

```bash
uv run python kb/ingest.py --config kb/config.yaml --rebuild
```

日常增量更新：

```bash
uv run python kb/ingest.py --config kb/config.yaml
```

## 诊断

```bash
uv run python kb/diagnose.py --config kb/config.yaml
```

确认高精度 reranker 可真实运行：

```bash
uv run python kb/diagnose.py --config kb/config.yaml --deep-reranker-check
```

## 查询例子

```bash
uv run python kb/query.py "项目有哪些关键风险？" --top-k 8
uv run python kb/query.py "最近一次会议决定了什么？" --top-k 8
uv run python kb/query.py "UAT 里程碑是什么？" --top-k 8
```

结果会返回 `source_path`、`heading`、`chunk_index`，并在存在时返回页码、幻灯片页码、sheet、cell range 和 OCR 标记。

## Codex / Kiro

模板已带 MCP 配置：

```text
.codex/config.toml
.kiro/settings/mcp.json
.kiro/steering/project-kb.md
AGENTS.md
```

在 Codex 中打开项目后运行 `/mcp`，确认能看到 `project-kb`。

提问例子：

```text
请先调用 search_project_kb，查一下当前项目的关键风险，并引用 source_path、heading、chunk_index。
```

MCP 只读，只提供：

```text
kb_status
search_project_kb
read_kb_source
```
