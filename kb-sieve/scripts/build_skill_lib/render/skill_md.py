from __future__ import annotations

from ..types import InputDoc
from ..utils.fs import normalize_title_whitespace

# Unified audit output directory for all kbtool commands.
RUNS_DIR = "runs/"


def _clean_title(value: str) -> str:
    return normalize_title_whitespace(value)


def render_generated_skill_md(
    skill_name: str,
    title: str,
    docs: list[InputDoc],
) -> str:
    doc_list = ", ".join(_clean_title(d.title) for d in docs[:5])
    if len(docs) > 5:
        doc_list += f" 等共 {len(docs)} 份文档"
    doc_count_word = f"{len(docs)} 份文档" if len(docs) > 1 else "全文"

    desc = (
        f"必用工具：对《{doc_list}》{doc_count_word}的任何提问，"
        "都必须先用 ./kbtool query 检索证据（返回 doc_id + 行号 + 匹配行），"
        "再用 ./kbtool read --around <line> 精读原文段落；"
        "答案必须引用 references/<doc_id>/doc.md 的原文，禁止凭记忆或外部知识回答。"
        f"触发：涉及《{doc_list}》中的人物/事件/术语/数据/章节/定义/流程/关系时必用本 skill。"
        "不触发：与本文档集无关的通用知识、创意写作、或常识性问题。"
    )
    frontmatter = f'---\nname: {skill_name}\ndescription: "{desc}"\n---\n\n'

    lines: list[str] = [frontmatter]
    lines.append(f"# {title}\n\n")
    lines.append(
        "> **核心原则**：查询是查询，生成是生成。所有结论必须来自 `runs/` 或 `references/` 的文本，不要凭记忆补全。\n\n"
    )

    # -- Default Flow --
    lines.append("## 默认流程\n\n")
    lines.append("```bash\n")
    lines.append("cd <本 skill 目录>\n")
    lines.append(f'./kbtool query --query "问题" --out {RUNS_DIR}r1.md\n')
    lines.append("```\n\n")
    lines.append(
        "- **查询**：`./kbtool query --query \"关键词\" --out runs/r1.md`\n"
        "  - stdout 返回 compact 摘要（文档 + 行号 + status），完整证据写入 `runs/*.md`。\n"
        "  - 返回格式：`[{file, doc_id, title, score, matches: [{line, text}]}]`\n"
        "  - 每个 match 是一行原文 + 1-based 行号。\n"
        "  - status: `high_confidence` / `needs_verification` / `no_hits`。\n"
    )
    lines.append(
        "- **精读原文（用 kbtool read）**：从 query 结果获取 `doc_id` 和 `line` 后，运行：\n"
        "  - `./kbtool read --doc-id <id> --around <line> --tokens \"关键词\"` — 自动定位到 line 所在完整段落\n"
        "  - `./kbtool read --doc-id <id> --find \"关键词\" --after <line>` — grep+read 一体，从指定行后搜索\n"
        "  - `./kbtool read --doc-id <id> --sections` — 列出文档所有章节标题+行号（文档地图）\n"
        "  - `./kbtool read --doc-id <id> --jump '210-230,450-460'` — 跳读多段不连续行\n"
        "  - `./kbtool read --doc-id doc1,doc2 --start 1 --count 20` — 多文档并行读取\n"
        "  - **禁止**使用 pi 自带的 read 工具读 references/ 文件。\n"
    )
    lines.append(
        '- **停止规则**：如果连续 **2 轮** query 都无命中，立即停止搜索，向用户报告"未找到相关证据"。'
        "不要无限换关键词尝试。\n"
    )
    lines.append(
        "- 最多 3 轮 query：R1 默认 quick；R2 换关键词或用 `--preset standard` 深查；R3 精读少量原文行。\n"
    )
    lines.append("- 回答必须引用 `references/...` 中的原文，未找到证据就明确说未找到。\n")
    lines.append(
        '    - **无例外规则**：无论问题看起来多么简单（如"有多少章节""文档标题是什么"），都**必须先**执行 `./kbtool query` 并将结果写入 `runs/*.md`。简单问题不是跳过 query 流程的理由。\n\n'
    )

    # -- Reasoning Chain --
    lines.append("## 推理链查询（关键）\n\n")
    lines.append("当问题包含多跳因果关系（A->B->C->...->答案）时，**禁止一次性查询所有关键词**。必须分轮迭代：\n\n")
    lines.append("1. **从起点开始**：每轮只查询 1-2 个环节的关键词。\n")
    lines.append("2. **从命中行发现线索**：读 `runs/*.md` 中的匹配行，提取新实体名/关键词。\n")
    lines.append("3. **用新线索推进**：将提取到的新关键词作为下一轮查询词。\n")
    lines.append("4. **保留审计轨迹**：每轮使用新的 `--out` 文件名（r1, r2, r3...）。\n")
    lines.append("5. **不跳过环节**：如果某一轮没有命中，调整关键词重新查询，直到确认这一跳。\n\n")
    lines.append("**分轮迭代示例**：\n")
    lines.append(
        f"- R1: `./kbtool query --query '角色A' --out {RUNS_DIR}r1.md` -> "
        "看到 [角色B, 物品X]，确认交好物件\n"
    )
    lines.append(
        f"- R2: `./kbtool query --query '角色B 物品X' --out {RUNS_DIR}r2.md` -> "
        "看到 [第三方C]，确认上门者\n"
    )
    lines.append(
        f"- R3: `./kbtool query --query '第三方C' --out {RUNS_DIR}r3.md` -> "
        "看到 [角色D, 受罚]，确认受罚者\n"
    )
    lines.append("- 加速收敛：发现线索密集但默认摘要不足时，用 `--preset standard` 深查。\n\n")

    # -- Common Commands --
    lines.append("## 常用命令\n\n")
    lines.append(f'- 快速检索：`./kbtool query --query "问题" --out {RUNS_DIR}r1.md`\n')
    lines.append(f'- 深查：`./kbtool query --query "关键词" --preset standard --out {RUNS_DIR}r2.md`\n')
    lines.append(f'- 指定文档范围：`./kbtool query --query "关键词" --doc-ids doc1,doc2 --out {RUNS_DIR}r3.md`\n')
    lines.append("- 精读原文：`./kbtool read --doc-id <id> --around <line> --tokens \"关键词\"`\n")
    lines.append("- 文档地图：`./kbtool read --doc-id <id> --sections`\n")
    lines.append("- 搜索+精读：`./kbtool read --doc-id <id> --find \"关键词\" --after <line>`\n")
    lines.append("- 跳读多段：`./kbtool read --doc-id <id> --jump '210-230,450-460'`\n\n")

    # -- Reading Spec --
    lines.append("## 精读规范（必须遵守）\n\n")
    lines.append(
        "1. **用 kbtool read 读取原文**：从 query 结果获取 `doc_id` 和匹配 `line`，运行\n"
        "   `./kbtool read --doc-id <id> --around <line> --tokens \"查询词\"`。\n"
        "   自动定位到完整段落、标记命中行。**不要**用 pi 自带的 read 工具。\n"
        "   - 先用 `--sections` 获取文档地图，了解整体结构。\n"
        "   - 用 `--around <line>` 自动读取匹配行所在的完整章节。\n"
        "   - 用 `--find \"词\" --after <line>` 从指定行后搜索关键词。\n"
        "   - 用 `--jump '210-230,450-460'` 一次跳读多个不连续段落。\n"
    )
    lines.append(
        "2. **优先读 `runs/*.md`**：query 的输出文件已经聚合了证据摘要，优先读这些审计文件。\n"
        "   只在需要更多上下文时才用 `read` 精读原文的指定行。\n"
    )
    lines.append(
        "3. **不猜测原文**：如果 query 未命中，不要凭记忆编造内容。明确告诉用户未找到相关证据。\n\n"
    )
    lines.append("### 正确 vs 错误示例\n\n")
    lines.append("**正确流程**：\n")
    lines.append('1. `./kbtool query --query "关键词" --out runs/r1.md`\n')
    lines.append("2. 读 `runs/r1.md`，看到匹配行的 doc_id 和 line 信息\n")
    lines.append('3. `./kbtool read --doc-id doc --around 218 --tokens "关键词"` 自动读取 line 218 所在的完整段落\n\n')
    lines.append("**错误流程**：\n")
    lines.append('1. `./kbtool query --query "关键词" --out runs/r1.md`\n')
    lines.append("2. 只看 stdout 摘要就回答 ← 未读完整证据\n")
    lines.append("3. 用 pi 自带的 `read` 工具读 references/ 文件 ← 绕过 kbtool read，失去命中标记\n\n")

    # -- Tuning --
    lines.append("## 调参原则\n\n")
    lines.append("- 默认参数就是推荐起步：`query` 默认 `--preset quick`，适合快速定位。\n")
    lines.append("- 加速收敛：发现线索密集时，用 `--preset standard` 获取更多上下文。\n")
    lines.append("- 只有确认证据不足时，再小幅增加 `--limit`（默认 10）或改用 `--preset standard`。\n\n")

    # -- Output Locations --
    lines.append("## 输出位置\n\n")
    lines.append(f"- `{RUNS_DIR}`：检索审计文件。\n")
    lines.append("- `references/<doc_id>/doc.md`：原始文档全文（按行号读取）。\n")
    lines.append("- `kb.sqlite`：FTS5 整文档索引。\n\n")

    # -- Document List --
    lines.append("## 文档列表\n\n")
    lines.append("| doc_id | 标题 | 原文路径 |\n|---|---|---|\n")
    for d in sorted(docs, key=lambda x: (x.doc_id, x.source_version)):
        doc_path = f"references/{d.doc_id}/doc.md"
        lines.append(f"| `{d.doc_id}` | {_clean_title(d.title)} | `{doc_path}` |\n")

    return "".join(lines)
