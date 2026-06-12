# Project KB 本地项目知识库模板

复制这个模板到任意项目根目录，把资料放进 `docs/`，重建索引后即可用命令行、Codex MCP 或 Kiro MCP 查询项目资料。

默认是 `balanced`：BGE-M3 embedding、RRF、不开 OCR、不开 cross-encoder，优先快速、低资源、稳定。

## 前置条件

```text
Python >= 3.11
uv
Git
网络连接
至少 10GB 可用磁盘空间
```

首次安装会从 PyPI 和 Hugging Face 下载依赖/模型。默认只需要 `BAAI/bge-m3`；`BAAI/bge-reranker-v2-m3` 只在 deep/accurate 模式使用。

安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.11
```

## 快速开始

让 AI 在项目根目录执行：

```bash
uv sync --extra ocr
mkdir -p docs
uv run python kb/doctor.py --config kb/config.yaml
```

把资料放入 `docs/` 后建立索引：

```bash
uv run python kb/ingest.py --config kb/config.yaml --rebuild
```

日常增量更新：

```bash
uv run python kb/ingest.py --config kb/config.yaml
```

如果全文检索异常，单独重建 FTS：

```bash
uv run python kb/ingest.py --config kb/config.yaml --rebuild-fts
```

## 查询

命令行：

```bash
uv run python kb/query.py "项目有哪些关键风险？"
uv run python kb/query.py "最近一次会议决定了什么？" --top-k 5
```

MCP 工具：

```text
search_project_kb_fast   默认工具，top_k=5、candidate_k=20、RRF、不返回完整 text
search_project_kb        fast alias，兼容旧调用
search_project_kb_deep   深度工具，top_k=8、candidate_k=50、BGE cross-encoder、不返回完整 text
read_kb_source           只在 snippet 不够时读取缓存文本，受 max_chars 限制
kb_status
```

默认让 AI 调 `search_project_kb_fast`。只有结果不足或明确要求高精度时再调 `search_project_kb_deep`。

## Profile

```bash
uv run python kb/profile.py set lite
uv run python kb/profile.py set balanced
uv run python kb/profile.py set accurate
```

- `lite`: `sentence-transformers/all-MiniLM-L6-v2`、384d、RRF、no OCR
- `balanced`: `BAAI/bge-m3`、1024d、RRF、no cross-encoder
- `accurate`: `BAAI/bge-m3`、BGE cross-encoder、deep mode

切换 embedding dimension 后必须重建索引：

```bash
uv run python kb/ingest.py --config kb/config.yaml --rebuild
```

## 卡顿处理

默认已限制单进程并发和底层数学库线程：

```text
max_concurrent_queries=1
TOKENIZERS_PARALLELISM=false
OMP_NUM_THREADS=1
MKL_NUM_THREADS=1
OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1
NUMEXPR_NUM_THREADS=1
```

注意：stdio MCP 的并发锁只在单个 MCP 进程内有效。多个客户端可能启动多个进程，模型也会被重复加载。未来可切换到 HTTP MCP 常驻服务来避免多进程重复加载。

如果仍然慢：

```bash
uv run python kb/profile.py set lite
uv run python kb/ingest.py --config kb/config.yaml --rebuild
uv run python kb/doctor.py --config kb/config.yaml
```

## 支持格式

```text
pdf, pptx, xlsx, docx, md, txt, csv, json, yaml, yml, py, ts, tsx, js, sql, toml, ini
```

默认 OCR 关闭，Office 图片提取关闭。需要 OCR 时在 `kb/config.yaml` 开启后重建索引。

## Codex / Kiro

模板已带：

```text
.codex/config.toml
.kiro/settings/mcp.json
.kiro/steering/project-kb.md
AGENTS.md
```

在 Codex 中打开项目后运行 `/mcp`，确认能看到 `project-kb`。
