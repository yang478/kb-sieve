---
name: kb-sieve
description: Use when generating an auditable, deterministic knowledge pack from one or more documents (txt/md/docx/readable-pdf), producing `references/` + `kb.sqlite` (FTS5, no embeddings) + a small `kbtool` CLI for deterministic query and read.
---

# Auditable Knowledge Pack Builder

Generate a `monitor`-style knowledge base skill from one or more documents:

- Progressive disclosure layout: `references/<doc_id>/{metadata.md,toc.md,doc.md}`
- SQLite index for fast non-vector search: `kb.sqlite` (CJK 2-gram + ASCII word tokens in FTS5)
- Deterministic runtime interface: `./kbtool query` for retrieval, `./kbtool read` for precise original-text reading
- Auditable chunk config: `chunking.json` (build-time chunk_size/overlap/separators)
- Optional sharded TSV indexes: `indexes/headings/*tsv`, `indexes/kw/*tsv` (fallback only)
  - `scripts/kbtool.py` and `scripts/kbtool_lib/*.py` are the python implementation
  - `bin/<platform>/kbtool(.exe)` is optional (PyInstaller); root `kbtool` wrapper prefers a fresh matching binary

## Runtime Interface

生成的 skill 暴露两个用户入口（一一对应、不可绕过）：

| 命令 | 用途 | 典型场景 |
|------|------|----------|
| `kbtool query --query "..." --out runs/r1.md` | FTS5/BM25 召回 + 确定性重排，返回 `doc_id + 行号 + 匹配行` 定位 | 默认检索入口：先用它定位证据 |
| `kbtool read --doc-id <id> --around <line> --tokens "..."` | 按 doc_id + 行号精读原文段落，自动段落定位、命中标记、grep+read 一体 | query 结果不足以确认细节时 |

## 最少参数（LLM 友好）

- **默认检索**：`kbtool query --query "..." --out runs/r1.md`（stdout 返回文档+行号定位，完整证据写入 `runs/r1.md`）
- **需要深查**：`kbtool query --preset standard --query "..." --out runs/r2.md`
- **精读原文**：从 query 输出取 `doc_id` + `line`，运行 `kbtool read --doc-id <id> --around <line> --tokens "关键词"`

**决策指南（由 AI Agent 根据上下文判断）：**

- **默认** → `query`
- **复杂问题** → 分 2-3 轮 `query`，每轮只推进 1-2 个关键词/实体
- **需要确认原文** → 从 query 输出中复制 `doc_id + line`，调用 `read --around`
- **无命中** → 换更宽泛或更贴近原文的关键词；连续 2 轮无命中就停止并说明未找到证据

**搜索实现：** 检索使用内置 SQLite FTS5，无需外部二进制依赖。

## Occam Chunking (Only)

- 唯一分块策略：递归字符分割（`\n\n` → `\n` → `。` `！` `？` `. ` `! ` `? ` → 空格 → 字符），以句子/段落为最小语义单位
- 每个 chunk 建立 `prev/next` 双向链表关系；检索命中后用 `--neighbors` 做邻居扩展
- 分块参数作为**构建时参数**提供，并写入产物 `chunking.json` 供审计/复现

## Quick Start

1. Choose an output skill name (lowercase letters/digits/hyphens only), e.g. `my-books`.
2. Run:
   - From this repo: `python3 pack-builder/scripts/build_skill.py --skill-name my-books --inputs /path/to/book1.pdf /path/to/book2.docx`
   - If installed under `.claude/skills`: `python3 .claude/skills/pack-builder/scripts/build_skill.py --skill-name my-books --inputs ...`
3. Use the generated skill at `.claude/skills/my-books/`.

## Command Reference

- Show help: `python3 .claude/skills/pack-builder/scripts/build_skill.py --help`
- Write to a specific directory: `--out-dir .claude/skills`
- Overwrite an existing output folder: `--force`
- Chunk tuning: `--chunk-size` (chars, default 1800 ≈ 450 tokens), `--overlap` (chars, default 0)

## What You Provide

- One file or many files via `--inputs` (supports `.md`, `.txt`, `.docx`, readable `.pdf`)
- Optional `--title` for the generated skill’s human-friendly heading

## Output Layout (Generated Skill)

```
.claude/skills/<skill-name>/
  SKILL.md
  kbtool                  # recommended entrypoint (POSIX wrapper)
  kbtool.cmd              # Windows wrapper
  kbtool.sha1             # stable hash of python sources
  kb.sqlite
  chunking.json           # auditable chunking config (chars + overlap + separators)
  bin/
    <platform>/           # optional per-platform binary build (PyInstaller)
      kbtool(.exe)
      kbtool.sha1         # copy of kbtool.sha1 for freshness check
  scripts/
    kbtool.py             # python entrypoint (deterministic)
    kbtool_lib/           # implementation modules (db/query/read/skill-json...)
    reindex.py            # TSV-only reindex helper (fallback)
  indexes/
    headings/             # sharded TSV title→path
    kw/                   # sharded TSV keyword→path (fallback only)
  references/
    <doc_id>/
      metadata.md
      toc.md
      doc.md              # original text, read by `kbtool read` with line numbers
```

## Robustness Rules (Do Not Skip)

- Prefer deterministic query output: run `./kbtool query --query "..." --out runs/r1.md`; use compact stdout for routing, then read the evidence summary in `runs/r1.md`.
- Keep LLM-facing parameters minimal: default query, optional `--preset standard`, then `read --around <line>` for precise原文.
- Do not tune advanced query parameters to pile on context; use `read` when exact wording is needed.
- Treat `indexes/*` as fallback only; never load a whole large index file if a smaller shard or `toc.md` suffices.

## Dependency Model (Cross-Platform)

- **Required:** `python3`
- **PDF (readable)**: prefers `pdftotext` (poppler-utils). If unavailable, the build fails with actionable instructions.
  - Optional fallback: pass `--pdf-fallback pypdf` (best-effort; requires `pypdf` installed).
- **DOCX:** uses a built-in OOXML extractor (no third-party Python deps); if extraction fails, instruct user to convert DOCX → MD/TXT.

## Pressure Scenarios (Self-Test)

- Missing dependencies: build from PDF on a machine without `pdftotext` (should fail with actionable instructions unless `--pdf-fallback pypdf` is enabled).
- Mixed inputs: build from `.md` + `.txt` + `.docx` in one run (should succeed).
- Rebuild safety: output skill folder already exists (should refuse unless `--force` is set).
- Version roll-forward: adjust `--chunk-size/--overlap` or inputs, then rerun the generator (use `--force` to overwrite).

## Common Mistakes

- Answering from compact stdout only: stdout is for routing; read `runs/*.md` or use `read --around` for exact evidence.
- Feeding a scanned PDF: this tool only supports *readable* PDFs; OCR first, or convert to TXT/MD.
- Assuming indexes are “the knowledge”: answers must cite `references/` files actually read; indexes are lookup only.
- Skipping query and reading references directly: must always run `./kbtool query` first to get evidence; then use `read --around` for precise原文. Do not read `toc.md` or `references/` files directly — this bypasses the retrieval audit trail.

## Red Flags (Stop and Fix)

- “I’ll just open the whole index, it’s easier” → split the question into smaller query rounds; never read indexes or references directly.
- “PDF import failed, so I’ll guess” → stop; convert PDF to text, install `pdftotext`, or try `--pdf-fallback pypdf` for readable PDFs.
