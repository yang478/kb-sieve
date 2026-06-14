# kb-sieve

**An LLM-friendly sieve for retrieval-augmented generation over auditable knowledge packs.**

kb-sieve builds self-contained knowledge-pack "skills" from source documents
and exposes a deliberately small retrieval surface — two CLI entries
(`kbtool query` and `kbtool read`) over a SQLite FTS5 index — that an LLM
agent can invoke as tools. The design follows the *sieve paradigm*: retrieval
should be **transparent, minimal, and auditable**, favoring simple lexical
filters (BM25, FTS5) over closed-box multi-stage pipelines.

## Repository layout

```
.
├── kb-sieve/              # Source: build pipeline + runtime templates
│   ├── SKILL.md           # Skill manifest (description for LLM agents)
│   ├── pyproject.toml     # Python package metadata
│   ├── scripts/           # build_skill.py + build_skill_lib/ (build pipeline)
│   └── templates/         # kbtool_lib/ (runtime library copied into each skill)
├── skills_example/        # Example built skill
│   └── min-fa-dian/       # Chinese Civil Code skill (中文民法典)
└── tech_report/           # Bilingual technical report
    ├── tech_report_en.md  # English version
    ├── tech_report_zh.md  # 中文版
    └── references.md      # Bibliography
```

## Quick start

Build a skill from one or more source documents:

```bash
cd kb-sieve
python3 scripts/build_skill.py \
  --skill-name my-kb \
  --inputs /path/to/document.md \
  --title "My Knowledge Base" \
  --out-dir /path/to/output
```

Use the generated skill:

```bash
cd /path/to/output/my-kb
./kbtool query --query "question" --out runs/r1.md
./kbtool read --doc-id <id> --around <line>
```

## Design

The runtime exposes **two** commands:

| Command | Role |
|---------|------|
| `kbtool query` | FTS5 BM25 + exact-identifier pre-match + line-numbered evidence + (optional) window-density rerank |
| `kbtool read` | Original-text reader with smart navigation (sections, find, jump, tokens) |

What is deliberately **absent**:

- No embeddings, no learned reranker
- No runtime memory or feedback loop
- No graph traversal at retrieval time
- No chunk-level index — retrieval unit is the whole document

The single augmentation over plain BM25 is **query-time window-density
reranking**, triggered only for long natural-language queries (≥ 4 ASCII
tokens) on multi-document corpora. It addresses a structural failure mode
of document-level BM25 where rare query tokens dominate the ranking. See
`tech_report/tech_report_en.md` § 4.7 for details.

## Documentation

The bilingual technical report describes the design philosophy, system
architecture, and an empirical comparison with a more complex predecessor
(`pack-builder`, also open-source). Key findings:

- kb-sieve is within **0.023 average MRR** of the complex pipeline across
  three datasets (`dream-of-the-red-chamber`, `anna-coulling`, `civil-code`)
- Window-density reranking closes 78% of the gap with the complex pipeline
- The rerank is a no-op on CJK short queries (verified harmless)

See `tech_report/tech_report_en.md` or `tech_report/tech_report_zh.md`.

## License

See repository files for license details.

## Acknowledgments

The work builds on insights from the predecessor `pack-builder` project
and the broader IR / RAG literature cited in `tech_report/references.md`.
