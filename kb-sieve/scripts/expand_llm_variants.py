#!/usr/bin/env python3
"""对已构建的知识库 skill 运行 LLM 术语变体扩写。

用法:
    python scripts/expand_llm_variants.py .pi/skills/hongloumeng
    python scripts/expand_llm_variants.py .pi/skills/hongloumeng --batch-size 15 --dry-run
"""
import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 术语变体扩写")
    parser.add_argument("skill_dir", type=str, help="skill 根目录（包含 kb.sqlite）")
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    skill_dir = Path(args.skill_dir)
    db_path = skill_dir / "kb.sqlite"
    if not db_path.exists():
        print(f"错误: {db_path} 不存在", file=sys.stderr)
        return 1

    # 备份原始数据库
    if not args.dry_run:
        import shutil
        backup_path = db_path.with_suffix(".sqlite.bak")
        shutil.copy2(str(db_path), str(backup_path))
        print(f"已备份到 {backup_path}")

    # 从 build_skill_lib 导入
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_skill_lib.doc.llm_variants import expand_surface_terms_from_db

    result = expand_surface_terms_from_db(
        str(db_path),
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    print(f"完成: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
