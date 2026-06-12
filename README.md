# Project KB 本地项目知识库模板

复制这个模板到任意项目根目录，把资料放进 `docs/`，重建索引后即可用命令行、Codex MCP 或 Kiro MCP 查询项目资料。

## 前置条件

需要：

```text
Python >= 3.11
uv
Git
网络连接
至少 10GB 可用磁盘空间
```

首次安装会从 PyPI 下载 Python 依赖，并从 Hugging Face 下载本地模型：

```text
BAAI/bge-m3
BAAI/bge-reranker-v2-m3
RapidOCR OCR 模型
```

这些模型下载完成后会使用本地缓存。知识库索引、OCR、reranker 都在本地运行。

安装 `uv`：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

如果机器没有合适的 Python，可以让 `uv` 安装：

```bash
uv python install 3.11
```

## 给 AI 的安装任务

让 AI 在项目根目录执行：

```bash
uv sync --extra ocr
uv run python kb/diagnose.py --config kb/config.yaml
mkdir -p docs
```

如果要接入 Codex 或 Kiro，再确认 MCP 配置文件存在：

```bash
ls .codex/config.toml .kiro/settings/mcp.json AGENTS.md
```

默认查询并发已经限制为 1：

```yaml
retrieval:
  max_concurrent_queries: 1
```

这个限制作用于同一个 MCP server 进程内的完整查询流程，包括 query embedding、LanceDB hybrid/vector search 和 reranker。多个独立终端进程不会共享这个锁。

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
