# Project KB 本地项目知识库模板

把这个模板复制到项目里，把资料放进 `docs/`，建立索引后即可用命令行、Codex MCP 或 Kiro MCP 查询项目资料。

默认 profile 是 `balanced`：BGE-M3 embedding、RRF、不开 OCR、不开 cross-encoder，优先快速、低资源、稳定。

## 前置条件

```text
Python 3.11
uv
Git
网络连接
至少 10GB 可用磁盘空间
```

首次安装会从 PyPI 和 Hugging Face 下载依赖/模型。默认只需要 `BAAI/bge-m3`；`BAAI/bge-reranker-v2-m3` 只在 deep/accurate 模式使用。

## 安装

推荐直接放在项目根目录：

```bash
uv python install 3.11
uv python pin 3.11
uv sync --extra ocr
mkdir -p docs
uv run project-kb-doctor --config kb/config.yaml
```

把资料放入 `docs/` 后首次建索引：

```bash
uv run project-kb-ingest --config kb/config.yaml --rebuild
```

日常增量更新：

```bash
uv run project-kb-ingest --config kb/config.yaml
```

全文检索异常时单独重建 FTS：

```bash
uv run project-kb-ingest --config kb/config.yaml --rebuild-fts
```

备用模块入口：

```bash
uv run python -m kb.ingest --config kb/config.yaml
uv run python -m kb.query "项目有哪些关键风险？"
uv run python -m kb.doctor --config kb/config.yaml
```

不要优先使用 `uv run python kb/*.py`，它在部分目录结构下可能触发 import 路径问题。

## 查询

命令行：

```bash
uv run project-kb-query "项目有哪些关键风险？"
uv run project-kb-query "最近一次会议决定了什么？" --top-k 5
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

## 让 AI 帮你使用

初始化安装提示词：

```text
请先阅读 README，然后在当前项目根目录安装 Project KB：
1. 确认使用 Python 3.11 和 uv。
2. 执行 uv python install 3.11、uv python pin 3.11、uv sync --extra ocr。
3. 运行 uv run project-kb-doctor --config kb/config.yaml。
4. 如果有 warning，请先解释原因，再给出处理建议。
```

首次索引提示词：

```text
请把项目资料整理到 docs/，然后运行：
uv run project-kb-ingest --config kb/config.yaml --rebuild
完成后用 uv run project-kb-query 做一次 smoke query，并报告 indexed_files、chunks、warnings。
```

日常更新提示词：

```text
请更新 Project KB 索引：
uv run project-kb-ingest --config kb/config.yaml
然后查询一个和本次新增资料相关的问题，确认能检索到新内容。
```

项目问答提示词：

```text
回答项目资料相关问题时，请先调用 search_project_kb_fast，默认 top_k=5。
回答必须引用 source_path、heading、chunk_index；如果有 page_number、slide_number、sheet_name、cell_range，也一起引用。
如果 fast 结果不足，再调用 search_project_kb_deep。不要并行调用多个 KB search。
```

故障诊断提示词：

```text
请运行 uv run project-kb-doctor --config kb/config.yaml，检查 config、DB、表、row count、FTS、vector search、模型和 MCP 配置。
如果出现慢、卡死、模型下载失败、FTS 或 MCP 配置问题，请按 README 的故障处理建议修复。
```

嵌套安装提示词：

```text
请判断我当前打开的是项目根目录，还是包含 KB 子目录的父目录。
如果 KB 安装在子目录，但 Codex/Kiro 打开的是父目录，请把 KB 子目录里的 .codex/config.toml 和 .kiro/settings/mcp.json 配置合并到父目录，cwd 指向 KB 子目录。
```

AI 使用边界：

```text
不要并行调用多个 KB search。
不要随意调用 read_kb_source；snippet 不够时才用，并限制 max_chars。
优先缩小 source_filter，而不是增加 top_k。
MCP 只读；重建、删除、重建 FTS 必须用命令行。
```

## Codex / Kiro

模板已带：

```text
.codex/config.toml
.kiro/settings/mcp.json
.kiro/steering/project-kb.md
AGENTS.md
```

MCP 使用模块方式启动：

```json
["run", "python", "-m", "kb.mcp_server"]
```

如果你从项目根目录打开 Codex/Kiro，配置会直接生效。打开后运行 `/mcp`，确认能看到 `project-kb`。

如果模板装在子目录，例如 `KB/`，但你从父目录打开 Codex/Kiro，子目录里的 MCP 配置不会自动生效。需要把 `.codex` 和 `.kiro` 配置复制或合并到父目录，并把 MCP `cwd` 改成 `KB`。

## Profile

```bash
uv run project-kb-profile set lite
uv run project-kb-profile set balanced
uv run project-kb-profile set accurate
```

- `lite`: `sentence-transformers/all-MiniLM-L6-v2`、384d、RRF、no OCR
- `balanced`: `BAAI/bge-m3`、1024d、RRF、no cross-encoder
- `accurate`: `BAAI/bge-m3`、BGE cross-encoder、deep mode

切换 embedding dimension 后必须重建索引：

```bash
uv run project-kb-ingest --config kb/config.yaml --rebuild
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

stdio MCP 的并发锁只在单个 MCP 进程内有效。多个客户端可能启动多个进程，模型也会被重复加载。未来可切换到 HTTP MCP 常驻服务来避免多进程重复加载。

如果仍然慢：

```bash
uv run project-kb-profile set lite
uv run project-kb-ingest --config kb/config.yaml --rebuild
uv run project-kb-doctor --config kb/config.yaml
```

## 诊断说明

```bash
uv run project-kb-doctor --config kb/config.yaml
uv run project-kb-diagnose --config kb/config.yaml --deep-reranker-check
```

`vector_index=not_reported_by_lancedb` 不代表向量查询不可用。请同时看 `vector_search_available`；如果它是 `true`，说明表内有 vector 字段且可执行向量搜索。当前默认不需要手动建 vector index。

## 支持格式

```text
pdf, pptx, xlsx, docx, md, txt, csv, json, yaml, yml, py, ts, tsx, js, sql, toml, ini
```

默认 OCR 关闭，Office 图片提取关闭。需要 OCR 时在 `kb/config.yaml` 开启后重建索引。
