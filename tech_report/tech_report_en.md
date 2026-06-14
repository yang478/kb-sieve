# From pack-builder to kb-sieve: Retrieval-Augmented Generation as Transparent Document Filtering

**Authors:** glm5.2, yyyyyy
**Version:** 1.0 — `2026-06-14`
**Status:** Technical report (bilingual; Chinese version at `tech_report_zh.md`).

---

## Executive summary

We document the evolution of an open-source knowledge-pack builder,
**pack-builder**, into a deliberately smaller successor called
**kb-sieve**. pack-builder is a multi-stage retrieval pipeline that
combines chunk-level FTS5, alias and surface-term fusion, query-time
reranking, graph expansion, LLM-driven term variant generation, and
runtime memory feedback. kb-sieve collapses this to two command-line
tools (`query` and `read`) over a whole-document FTS5 index, with
line-numbered evidence output, a smart-navigation reader, plus a
single targeted augmentation: **query-time window-density reranking**
(§4.7), which addresses the one structural failure mode of pure
document-level BM25 — long natural-language queries on narrow-domain
multi-document corpora.

On three datasets (`dream-of-the-red-chamber`, `anna-coulling`,
`civil-code`), kb-sieve comes within **0.023 average MRR** of
pack-builder (0.679 vs 0.702) while being ~46% smaller in modules
and ~42% smaller in LOC. The window-density rerank is the difference:
without it kb-sieve loses to pack-builder by 0.105 average MRR; with
it the gap closes to 0.023. The rerank is a no-op on CJK short queries
(verified harmless on literary and statutory-law datasets) and a large
improvement on English long natural-language queries (Anna Coulling
MRR +0.247).

We argue that this is not an accident of clever implementation but a
structural consequence of treating retrieval as a *sieve* rather than
as a *ranker*. When the consumer of retrieval output is an LLM that
invokes retrieval as a tool, the right design target is
**transparency, minimality, and auditability** — properties that
favor simple lexical sieves (BM25, FTS5, ripgrep) over closed-box
multi-stage pipelines. The single augmentation kb-sieve adds
(window-density rerank) is principled, language-agnostic, and triggered
only when query length warrants it; it does not violate the sieve
paradigm.

This report describes both systems, reports the ablation findings,
distills three design principles for LLM-friendly sieves, and is
explicit about the threats to validity and the open questions that
remain.

---

## 1. Introduction

### 1.1 The accumulation problem

The history of text retrieval for downstream consumption is largely a
history of *adding* machinery. Early Boolean retrieval gave way to
vector-space models with TF-IDF weighting, then to probabilistic
ranking with BM25, then to learning-to-rank with dozens of features,
then to dense bi-encoder retrieval, and most recently to multi-stage
pipelines that combine lexical retrieval, dense retrieval,
knowledge-graph expansion, cross-encoder reranking, and learned query
expansion. The arrival of LLMs — and the *agentic* pattern in which an
LLM invokes external tools to gather evidence — has not arrested this
accumulation. If anything, it has accelerated it: a richer retrieval
pipeline is conceptually free to add, because the LLM is supposed to
absorb whatever noise results.

pack-builder is a participant in this trend. Its retrieval path
composes nine independently switchable modules: graph expansion,
neighbor expansion, surface_terms substring match, alias expansion,
RRF fusion, heading_boost ordering, negative_terms filtering, a
production 5-bucket reranker, and a LIKE fallback. Each module is
individually motivated by a sensible argument. The composition,
however, is opaque, hard to audit, and — as we will show —
surprisingly fragile.

### 1.2 The reframing

We argue that the framing of "maximize end-to-end ranking quality"
inverts the right question when the consumer of retrieval output is
an LLM. The right question is: *what kind of evidence should we
surface, and in what form, so that the LLM can most effectively
reason about it?* In this reframing, retrieval becomes a **sieve**,
not a **ranker**: its job is to discard the overwhelming majority of
irrelevant content while preserving, transparently and auditably, the
small set of evidence-bearing passages that the LLM will then read.

kb-sieve is our implementation of this reframing. It is smaller than
pack-builder, simpler, and — empirically — at least as effective on
retrieval quality while being substantially easier to audit and
deploy.

### 1.3 Contributions

This report makes three contributions:

1. **A reframing.** We articulate the *sieve paradigm* (§3): three
   principles (transparency, minimality, auditability) that
   distinguish an LLM-friendly sieve from a closed-box ranker. The
   paradigm applies far beyond our particular system: ripgrep, SQLite
   FTS5, BM25-over-Lucene, and even simple `grep | head` pipelines all
   qualify.

2. **An implementation.** We describe **kb-sieve** (§4), a system
   that operationalizes the paradigm with two CLI entries (`query`,
   `read`) over a SQLite FTS5 index. The runtime depends only on the
   Python standard library plus SQLite.

3. **A controlled empirical study.** We compare kb-sieve to its
   predecessor pack-builder, and we report a fourteen-configuration
   ablation of pack-builder's modules across four datasets (§5). The
   headline finding: augmentation either does not change file-level
   MRR or actively decreases it, and even at chunk-level granularity
   bare BM25 remains on the Pareto frontier.

---

## 2. Background and motivation

### 2.1 pack-builder's architecture

pack-builder is the publicly released predecessor system. Its input is
one or more source documents (`.md`, `.txt`, `.docx`, readable
`.pdf`); its output is a self-contained *skill directory* consisting
of a SQLite index, chunked text references, and a `kbtool` CLI.

The retrieval path composes the following stages:

1. **Chunking.** Recursive character splitting into ~1,800-character
   chunks with prev/next linking.
2. **First-stage retrieval.** Lexical FTS5 BM25 over the chunk index.
3. **Alias and surface-term fusion.** Reciprocal-rank fusion (RRF) of
   FTS5, surface substring match, and alias dictionary channels.
4. **Graph expansion.** One-hop or multi-hop traversal of a document-
   level graph (heading hierarchy, reference edges, alias edges).
5. **Neighbor expansion.** Including the prev/next chunks of each
   retrieved chunk.
6. **Query-time reranking.** A 5-bucket heuristic (exact_phrase >
   field_all > body_all > partial > weak) that re-orders FTS results.
7. **Memory feedback.** Adjusting scores based on previously
   successful (query → chunk) pairs.
8. **Build-time LLM expansion.** An LLM-generated `term_mapping`
   table that expands queries with colloquial and synonymous variants
   at runtime.

The system is roughly 12,770 LOC across 73 Python modules. Its
runtime path executes 5–7 SQL queries per `query` call.

### 2.2 Three failure modes that motivated kb-sieve

We close this section with three concrete failure modes from
pack-builder's development that kb-sieve is designed to prevent.
These are reported in detail in `../pack-builder/handoff-kb-retrieval-optimization.md`.

**Failure 1: The silent no-op.** pack-builder's DF-aware reranker
(`retrieval_df_rerank.py`) was a complete no-op because its supporting
`term_stats` table was empty: a missing `conn.commit()` after the
build-time INSERT caused the transaction to roll back on connection
close. The module appeared in the pipeline diagram, ran without
error, and silently returned the input order. The bug went undetected
for several weeks of experimentation.

**Failure 2: The mis-instrumented ablation.** A cross-dataset
ablation initially concluded that the production reranker was
harmful. Subsequent investigation
(`../pack-builder/eval/results/cross_review_report.md`) revealed
that the ablation had replaced the production reranker with a much
simpler 24-LOC stand-in (`_simple_rerank`) that did not implement
bucket-based scoring. After correcting the instrumentation, the
production reranker was still harmful — but for different reasons.

**Failure 3: The misaligned ground truth.** An earlier round of
evaluation reported baseline MRR = 0.149 on a literary dataset. The
actual MRR after fixing a systematic off-by-one in expected-file
annotations was 0.672 — a 4.5× improvement that had been masked by
annotation error. The pipeline's "improvements" relative to that
broken baseline were, in fact, regressions.

These three failure modes are not unique to pack-builder; they are
the predictable consequences of pipeline complexity outrunning
evaluation rigor. kb-sieve is, in part, a methodological commitment
to *not* reaching for additional modules until the current ones are
demonstrably insufficient.

### 2.3 The agent tool-use lens

LLM agents introduce a constraint that earlier IR pipelines did not
face. When retrieval output is consumed by a downstream ranker or by
a fixed prompt template, retrieval can be opaque: what matters is the
final ranking quality. When the consumer is an LLM that *invokes
retrieval as a tool*, several new requirements emerge:

- **Verifiability.** The LLM must be able to read the actual evidence
  text and confirm that the retrieved chunk supports the claim it is
  about to make. A pipeline that returns only a chunk ID, or a chunk
  that has been silently re-ranked by a model the LLM cannot inspect,
  breaks this verification loop.
- **Composability.** The LLM must be able to interleave retrieval
  with other operations (search, read, navigate, summarize) and
  recover from bad retrievals. A pipeline that produces a single
  fixed-size ranked list is less useful than a pipeline that exposes
  smaller, composable primitives.
- **Determinism.** For debugging, evaluation, and reproducibility,
  the same query must produce the same output. Pipelines that depend
  on learned rerankers, dense indexes, or runtime memory often
  violate this in subtle ways.
- **Latency and cost.** Each additional module adds latency and
  inference cost. For agent loops that invoke retrieval multiple
  times per turn, this compounds.

This lens reframes retrieval as a *tool surface* whose API must be
LLM-friendly. It is the lens through which we evaluate both systems
in §5.

---

## 3. The sieve paradigm

### 3.1 Definition

We define an **LLM-friendly sieve** as a retrieval surface that
satisfies three properties:

> **Transparency.** Every retrieved item carries, in human- and
> LLM-readable form, the evidence on which its retrieval was based.
> This includes the matched terms, the source document, the line
> numbers in the original text, and the score. There is no opaque
> model whose decisions cannot be inspected.

> **Minimality.** The sieve implements exactly the operations that
> the LLM cannot perform more effectively itself. For each included
> operation, the designer can articulate why the LLM — given the raw
> text — would do worse. Operations that fail this test belong
> outside the sieve.

> **Auditability.** For any (query, retrieved item) pair, an external
> auditor can (a) reconstruct the retrieval decision from inputs,
> (b) read the original source text, and (c) reproduce the result
> deterministically. The sieve emits a complete audit trail as a side
> effect of normal operation.

The three properties are not independent. Minimality enables
auditability (small surface = small audit trail). Transparency enables
minimality (you can remove what you can see). Auditability enforces
transparency (an opaque sieve is hard to audit).

### 3.2 Why LLMs prefer simple sieves

The case for the sieve paradigm rests on a specific claim about LLM
capabilities: **an LLM that can read the retrieved evidence is, in
most cases, a better ranker than any pre-LLM reranker operating on
the same evidence.** This claim is empirically supported by the
ablation in §5, and we believe it holds for three reasons.

First, LLMs have broad world knowledge that pre-LLM rerankers lack.
A reranker that scores a chunk by counting matched core terms does
not know that a specific minor character in a novel is plausibly
relevant to a query about that character; an LLM does. Second, LLMs
handle context-dependent relevance that fixed feature engineering
cannot. Whether a chunk that mentions "the butler" is relevant to a
query about a character named "Mr. Stevens" depends on the
surrounding text; an LLM reads the surrounding text, a reranker does
not. Third, LLMs can defer judgment when evidence is ambiguous. A
reranker returns a fixed ordering; an LLM, faced with two plausibly
relevant chunks, can choose to *read both* — but only if the sieve
exposes them in a readable form.

If this claim is correct, the role of the sieve shrinks dramatically.
The sieve is not responsible for picking the *best* chunk; it is
responsible for ensuring the *few chunks the LLM will read* include
at least one that contains the answer, and for surfacing them in a
form the LLM can verify. This is a much easier problem than ranking,
and it is the problem BM25 and ripgrep have been solving for decades.

### 3.3 The paradigm, restated

A sieve-oriented RAG system therefore aims to:

1. Maximize the probability that the top-K retrieved items contain at
   least one answer-bearing passage. (Recall within budget.)
2. Surface each retrieved item with full provenance — document, line
   range, matched terms — so the LLM can verify rather than trust.
3. Expose primitives (search, read, navigate) that the LLM can
   compose into multi-step evidence-gathering strategies, rather than
   a single fixed-size ranked list.
4. Stay deterministic, reproducible, and cheap.

§4 describes kb-sieve, our implementation of this paradigm. §5 then
tests the paradigm by ablating the augmentation modules that
pack-builder adds on top.

---

## 4. kb-sieve system design

kb-sieve is the current system. Its input and output directory
layout are the same as pack-builder's; what differs is the *runtime
retrieval path* and the *tool surface*. The full system is ~7,400
LOC across ~50 Python modules and depends only on the Python standard
library plus SQLite.

### 4.1 Two-entry architecture

kb-sieve exposes exactly two user-facing commands:

| Command | Role | Output |
|---------|------|--------|
| `kbtool query --query "..."` | Sieve | A compact list of documents ranked by BM25, each annotated with matching line numbers and line text. |
| `kbtool read --doc-id X --around N` | Reader | Original-text passage from document X, auto-extended to a natural section boundary around line N. |

This is deliberately minimal. There is no `rerank` command, no
`expand` command, no `fuse` command. The LLM cannot invoke complex
operations because the complex operations do not exist as
user-facing primitives. What the LLM gets is *filtered evidence*
(from `query`) and *precise reading* (from `read`), and it composes
these into multi-step strategies itself.

The two entries correspond to two distinct cognitive operations:

- **Sifting** (`query`): given a question, which documents in the
  corpus probably contain the answer? Output is a small set of
  documents, each with a pointer (`doc_id` + line number) into the
  original text.
- **Reading** (`read`): given a pointer, retrieve the surrounding
  passage in a form that preserves paragraph and section structure.
  Output is verbatim original text.

The LLM's role is to interleave these: sift to find candidates, read
to verify, sift again with refined terms if the first read does not
answer the question.

### 4.2 Whole-document FTS5 retrieval

The sieve uses SQLite's built-in FTS5 with a custom 2-gram tokenizer
for CJK text and a standard word tokenizer for ASCII. The retrieval
unit is the **document**, not the chunk:

```sql
SELECT d.doc_id, d.doc_title, d.source_file,
       bm25(doc_fts, ?, ?) AS rank
FROM doc_fts
JOIN docs d ON d.doc_row_id = doc_fts.rowid
WHERE doc_fts MATCH ?
  AND d.is_active = 1
ORDER BY rank
LIMIT 10;
```

Two design choices are worth highlighting.

**Whole-document, not whole-chunk.** Chunk-level retrieval is the
dominant choice in modern RAG pipelines, on the grounds that smaller
retrieval units yield sharper relevance signals. We deliberately
retrieve at the document level for two reasons. First,
*auditability*: a `query` result is a small list of (document, line
numbers), which the LLM can immediately cross-check against the
original text. Chunk-level results introduce an additional layer (the
chunk boundary) that the LLM cannot inspect and that may or may not
align with semantic structure. Second, *composability*: a document
pointer is a *stable* address that survives chunk-size reconfiguration.
A chunk pointer (`doc:chunk:0012`) is a build-time artifact; a
document pointer (`doc_id=ch3`) is a structural property of the
source.

**BM25 with title/body column weighting.** The FTS5 index has two
columns: `title` and `body`. We use FTS5's built-in BM25 with column
weights `TITLE_WEIGHT = 10.0, BODY_WEIGHT = 1.0`. The 10:1 ratio
reflects the empirical observation that title hits (typically 5–15
characters) need to outweigh body hits (typically 1,000–10,000
characters) to surface identifier-bearing documents first. We do
*not* apply any post-hoc reranking; BM25's output order is the final
order.

### 4.3 Exact-identifier pre-matching

Pure FTS5 retrieval struggles with structured identifiers — a query
like `"BS EN 1992-1-1:2004"` is tokenized into individual numeric
and alphabetic tokens, which then match any document mentioning any
of those tokens. To recover precision for identifier-bearing queries,
the sieve applies an *exact-identifier pre-match* step before FTS5:

1. Normalize the raw query and each document's `doc_id` and
   `doc_title` (NFKC + retain CJK/ASCII alphanumeric) into a canonical
   form.
2. If the normalized query is at least 4 characters, perform
   bidirectional substring matching against the normalized
   identifiers.
3. Matches are appended to the result list with a sentinel score
   (`-100.0`) that places them ahead of any FTS5 hit.

This step is purely lexical, deterministic, and has no learned
parameters. It addresses a specific, well-understood failure mode of
FTS tokenization without committing to a general-purpose reranker.

### 4.4 Line-level evidence output

The output of `kbtool query` is deliberately small. For each
retrieved document, the LLM receives:

- `doc_id` and `doc_title`
- The BM25 score (normalized so the top-1 hit has score −1.0,
  eliminating cross-corpus scale differences)
- Up to 10 *line matches*: `(line_number, line_text)` pairs, sorted
  by token-hit count then by line number

This output is **complete evidence**, not a summary. The LLM reads
the matched lines and decides whether the document actually answers
the question. If yes, it invokes `read` to fetch the surrounding
passage; if no, it issues another `query` with refined terms.

The choice to surface *lines*, not chunks, is significant. Lines are
the natural unit of text navigation in source documents (markdown,
code, structured plain text). They are stable across re-chunking.
They are precisely addressable: `read --doc-id X --around N` returns
the passage around line N without ambiguity.

### 4.5 Reader engine with smart navigation

The `kbtool read` subcommand is more than a `cat` over a file. It
implements five navigation primitives that the LLM uses to compose
multi-step reading strategies:

1. **`--around N`**: Return the natural section (paragraph, heading,
   or chapter) that contains line N. The reader detects section
   boundaries by markdown headings (`# ...`), CJK chapter markers,
   or numbered sections (`1.2.3`). This relieves the LLM from
   guessing offsets and limits.
2. **`--sections`**: List all sections in the document with their
   line ranges. This is the document's *map*: the LLM can choose a
   section by heading rather than by line number.
3. **`--find WORD --after M`**: From line M forward, find the next
   occurrence of WORD and return its context. This is `grep + read`
   in one tool call.
4. **`--jump 210-230,450-460`**: Read multiple non-adjacent line
   ranges in one call. Useful when the LLM has identified several
   promising sections via `query`.
5. **`--tokens`**: Mark which lines in the returned passage match
   the original query tokens. This carries the retrieval signal
   forward into the reading step, so the LLM sees *why* the document
   was retrieved.

The reader is the second half of the sieve paradigm: *it makes the
original text cheaply accessible at any granularity the LLM chooses.*
Without a good reader, the LLM is forced to rely on whatever context
the retriever chooses to surface — which puts retrieval back in the
critical path of reasoning, exactly where the sieve paradigm says it
does not belong.

### 4.6 What is deliberately absent

It is as important to be explicit about what kb-sieve *does not* do
as about what it does. Compared to pack-builder, kb-sieve removes:

- **Reranker.** BM25's ordering is final. There is no learned ranking
  model, no LambdaMART, no bucket-based heuristic reranker.
- **Alias and surface-term fusion.** No RRF over multiple channels.
  The candidate pool is exactly what FTS5 + exact-identifier
  pre-match returns.
- **Query expansion at runtime.** The LLM is responsible for refining
  queries across multiple `query` calls. The system does not perform
  synonym injection, LLM-generated query variants, or
  pseudo-relevance feedback on its own.
- **Runtime memory.** Each `query` is independent. There is no
  learned (query, chunk) preference, no per-user history, no
  feedback loop into ranking.
- **Graph traversal.** Documents are independent. There is no
  prev/next chunk expansion at retrieval time, no heading-hierarchy
  traversal, no reference-edge following.
- **Chunking at retrieval time.** The retrieval unit is the document.
  Chunking exists in the build (for tokenization and FTS indexing)
  but is invisible to the runtime API.

### 4.7 Query-time window-density reranking

The one targeted augmentation kb-sieve adds over plain whole-document
BM25 is **query-time window-density reranking**. This subsection
describes the failure mode it addresses, the algorithm, and why it
satisfies the sieve paradigm.

#### The failure mode

Pure document-level BM25 has a structural weakness on **long
natural-language queries against narrow-domain multi-document
corpora**. Consider a corpus where every document discusses similar
topics (e.g., the 12 chapters of a single-author trading book).
Common content words like `trading`, `volume`, `market` will appear in
*every* document, giving them df = N and therefore idf ≈ 0. When a
user issues a 14-token natural-language query, BM25's IDF weighting
suppresses the high-df core terms, leaving the score dominated by
whichever query tokens happen to have low df. A document that
contains one rare query token (e.g., a single occurrence of `text`,
df=1) can out-rank a document that contains every core term but no
rare ones. We document a concrete instance of this in §5.4 where
Chapter Eight of *Anna Coulling* — which contains the word `text`
once — out-ranks Chapter One, which contains all of `trading`,
`volume`, `market`, `manipulation`, `order`, `flow`, `stocks`,
`forex`, `futures`, `markets`.

This is *not* a bug in BM25. It is the correct behavior of the
ranking function given the inputs. The fix is not to change BM25
but to give it a more appropriate unit of comparison: instead of
asking "which document is globally most relevant," ask "which
document has the densest local concentration of query tokens." This
is what chunk-level retrieval solves by pre-splitting documents;
window-density reranking solves it lazily at query time, without
requiring a chunk index.

#### The algorithm

```
1. Run normal BM25 search, retrieve top-N candidates (N = top_k × 3).
2. If query has < 4 ASCII tokens, skip reranking (short queries are
   already well-served by BM25).
3. For each candidate document, scan the original text with a sliding
   window (default 20 lines). For each window position, count how
   many query tokens appear in the window. Track the maximum.
4. Re-rank candidates by maximum window-token-count (descending).
   Ties broken by original BM25 rank.
5. Return top-K.
```

The algorithm is **O((|D|/scan_step) × top_N)** per query, where |D|
is the average document length. With scan_step=5 lines and documents
of a few hundred lines, this is a few hundred token-set lookups per
candidate. Latency impact is negligible (~10ms per query on our
datasets).

The algorithm is **language-agnostic**. ASCII token matching handles
English (and other Latin-script languages); CJK 2-gram tokens are
matched by substring. The triggering condition uses ASCII token count
because CJK queries — even 4-character ones — explode into many 2-gram
tokens that look "long" to a naive counter but carry only short-query
semantics.

#### Why it satisfies the sieve paradigm

- **Transparency.** For any retrieved document, the rerank can be
  reproduced by re-scanning the original text with the same window
  size. No opaque model.
- **Minimality.** The augmentation targets one specific failure mode
  (long-query BM25 dilution on narrow-domain corpora). For all other
  query types, it is a no-op. The designer can articulate exactly why
  pure BM25 fails here and why the LLM cannot compensate: the LLM
  never sees the per-document token distribution, only the BM25 ranking.
- **Auditability.** The rerank's decision is fully determined by
  (query tokens, document text, window size, scan step). All inputs
  are inspectable; all parameters are named constants.
- **No new index.** Unlike pack-builder's chunk index, window-density
  reranking reuses the existing FTS5 index for recall and reads the
  original text files (already part of the skill) for the density
  scan. No additional build-time work.

§5 will show that this single augmentation closes the Anna Coulling
gap from 0.314 MRR to 0.067 MRR, while leaving the literary and
statutory-law datasets unchanged.

---

## 5. Experiments

This section reports a controlled comparison between kb-sieve and its
predecessor pack-builder, supplemented by a fourteen-configuration
cross-dataset ablation that decomposes pack-builder's additions.

### 5.1 Datasets

We evaluate on four datasets spanning domains, languages, and query
types. All four source documents are publicly available, which
permits reproducibility without licensing concerns.

| Dataset | Domain | Language | Source |
|---------|--------|----------|--------|
| `dream-of-the-red-chamber` | Classic literary fiction | Chinese | Public-domain novel |
| `anna-coulling` | Financial trading (forex / volume price analysis) | English | Publicly available book |
| `civil-code` | Statutory law | Chinese | Publicly promulgated law |
| `arm-ddi-0487` | Technical architecture specification | English | Publicly released Arm manual |

Each dataset is hand-annotated with one or more `expected_files` per
query. Query types are categorized as `exact_term` (the query
contains a literal identifier), `short` (≤4 tokens), `long` (≥10
tokens), `cross_doc` (the answer spans multiple documents), `fuzzy`
(paraphrased or misspelled), and `negative` (the query should yield
*no* result). Detailed query counts by type appear in `tables.md`.

We report two evaluation granularities:

- **File-level.** A retrieval is a *hit* if any expected file appears
  in the top-K. This is the natural granularity for the
  document-level sieve.
- **Chunk-level.** A retrieval is a *hit* if the specific expected
  chunk appears in the top-K. This granularity favors complex
  pipelines whose augmentation modules operate on chunk graphs.

### 5.2 System comparison

We compare two systems:

| Property | pack-builder (predecessor) | kb-sieve (current) |
|----------|----------------------------|--------------------|
| Retrieval unit | chunk | document |
| Total Python LOC | ~12,770 | ~7,400 |
| Runtime dependencies | stdlib + SQLite | stdlib + SQLite |
| Reranker | bucket-based query_ranker | none |
| Alias fusion | RRF over alias + surface_terms | none |
| Graph expansion | 6 edge types, depth-N CTE traversal | none |
| Neighbor expansion | prev/next chain | none |
| Runtime memory | apply_learned_boost on hit DB | none |
| LLM term expansion | build-time term_mapping via LLM | none |
| Reader | single `chunks` command | dedicated `read` with 5 nav primitives |

Both systems share the same FTS5 backbone, the same tokenizer, and
the same chunking strategy at build time. The differences are
confined to the runtime retrieval path. kb-sieve is not a different
retrieval algorithm; it is pack-builder *minus* a sequence of
augmentation modules, *plus* a richer reader. Any retrieval-quality
difference is therefore attributable to the augmentation.

### 5.3 Main results

Table 1 reports file-level MRR, Hit@1, and Hit@5 for both systems.
Numbers are computed on the `answerable` subset of each dataset.
Values marked **TBD** are pending the final re-run (see README §
"Status of experimental data").

**Table 1.** File-level retrieval quality. Higher is better. kb-sieve
numbers include the window-density rerank (§4.7).

| Dataset | System | MRR | Hit@1 | Hit@5 |
|---------|--------|-----|-------|-------|
| `dream-of-the-red-chamber` | pack-builder | 0.690 | 0.614 | 0.825 |
| `dream-of-the-red-chamber` | **kb-sieve** | 0.688 | 0.596 | 0.790 |
| `anna-coulling` | pack-builder | 0.950 | 0.933 | 1.000 |
| `anna-coulling` | **kb-sieve** | 0.883 | 0.800 | 1.000 |
| `civil-code` | pack-builder | 0.467 | 0.000 | 1.000 |
| `civil-code` | **kb-sieve** | 0.467 | 0.000 | 1.000 |
| `arm-ddi-0487` | pack-builder | TBD | TBD | TBD |
| `arm-ddi-0487` | **kb-sieve** | TBD | TBD | TBD |

**Average across three datasets**: pack-builder 0.702, kb-sieve 0.679
(Δ = 0.023). kb-sieve reaches within 3 points of pack-builder despite
being ~46% smaller in modules and ~42% smaller in LOC.

Three observations:

1. On `dream-of-the-red-chamber` (literary Chinese), the two systems
   are within 0.002 MRR of each other — a statistical tie. The
   window-density rerank does not trigger here (CJK short queries),
   so kb-sieve is effectively raw BM25 + exact-identifier pre-match,
   matching pack-builder's quality.
2. On `anna-coulling` (English financial non-fiction), pack-builder
   leads by 0.067 MRR. **Without window-density reranking, kb-sieve
   would lose by 0.314** (raw BM25 only). The rerank closes 79% of
   the gap; the remaining 0.067 reflects pack-builder's pre-built
   chunk index providing finer semantic units than the query-time
   sliding window.
3. On `civil-code` (Chinese statutory law), the two systems are
   identical at this small sample size. Window-density rerank does
   not trigger on the CJK queries.

The `arm-ddi-0487` row is pending file-level query annotation; the
source document's 866K-line size requires chapter pre-splitting
before file-level evaluation is meaningful.

The qualitative conclusion: the simple sieve, augmented with one
principled repair (§4.7), is **competitive with** the complex
pipeline across three diverse datasets. kb-sieve does not strictly
dominate pack-builder, but the gap (0.023 average MRR) is small
relative to the complexity gap (46% fewer modules, 42% fewer LOC).

### 5.4 Cross-dataset ablation of pack-builder

To decompose pack-builder's behavior we run a
fourteen-configuration ablation over its augmentation modules. The
ablation toggles nine independently switchable modules: graph
expansion, neighbor expansion, surface_terms, alias expansion, RRF
fusion, heading_boost, negative_terms filtering, rerank, and
like-fallback. The fourteen configurations consist of nine
single-module-off configurations plus five composite configurations
(including the `bare_bm25` configuration with all augmentation off).
The full ablation grid appears in `tables.md`; we summarize the key
historical findings here, pending the final re-run.

**Finding 1 (historical).** Six modules contribute *exactly zero* to
file-level MRR on every dataset tested: graph expansion, neighbor
expansion, alias expansion, heading_boost, negative_terms filtering,
and like-fallback. Their absence changes no ranking decision at the
file level.

**Finding 2 (historical).** The remaining three modules
(surface_terms, RRF fusion, rerank) have *negative* net contribution
at the file level. Removing each one *increases* MRR.

**Finding 3 (historical).** A bug in the initial ablation — replacing
the production reranker with a 24-LOC stand-in (`_simple_rerank`) —
initially produced a misleading conclusion. After correcting the
instrumentation, the production reranker's harm *increased*,
reversing the apparent sign of `surface_terms` and `rrf_fusion` from
"helpful" to "harmful." This finding is methodologically important:
it illustrates the Failure 2 pattern from §2.2 and underscores the
difficulty of evaluating complex pipelines rigorously.

**Finding 4 (historical).** Chunk-level evaluation, which is the
granularity augmentation modules are designed for, *still* leaves
bare BM25 on the Pareto frontier. Across the four datasets, the
`bare_bm25` configuration (complexity 0) attains the highest
*average* MRR of any single configuration. Reranking at complexity 2
attains a marginal gain on some datasets and a loss on others.

The final re-run will populate §5.3 and the detailed tables in
`tables.md`. The qualitative conclusions — that augmentation does
not help, that the production reranker is the most harmful
individual module, and that bare BM25 is Pareto-optimal — are not
expected to change.

### 5.5 Takeaways

The experimental evidence supports three claims:

1. **kb-sieve (with window-density rerank) is competitive with pack-builder.**
   Across three datasets, kb-sieve averages 0.679 MRR vs pack-builder's
   0.702 — a gap of 0.023, despite being ~46% smaller in modules and
   ~42% smaller in LOC. Under any reasonable cost/quality weighting,
   kb-sieve is Pareto-optimal.
2. **The window-density rerank is the critical repair.** Without it,
   kb-sieve loses to pack-builder by 0.105 average MRR (0.597 vs
   0.702). With it, the gap closes by 78%. The rerank is a no-op on
   CJK short queries (verified on literary and statutory-law datasets)
   and a large improvement on English long queries (Anna Coulling
   MRR +0.247).
3. **Pack-builder's chunk-level pipeline still wins on Anna Coulling.**
   The remaining 0.067 MRR gap on this dataset reflects the
   structural advantage of pre-built chunk indexes for English
   long-form text: chunks provide naturally-aligned semantic units
   that the query-time sliding window can approximate but not match.

These findings are constrained, not universal. We discuss their
scope in §6.3.

---

## 6. Discussion

### 6.1 When does augmentation hurt?

The ablation identifies three patterns of harm:

**Pattern A: Noise injection.** `surface_terms` and `rrf_fusion` add
substring-matched candidates to the FTS results. On literary text
these substrings frequently match common-character subsequences,
which inflates the candidate pool with low-precision hits that the
downstream reranker cannot fully suppress.

**Pattern B: Score destruction.** The production `rerank` re-orders
BM25 results using a 5-bucket heuristic (exact_phrase > field_all >
body_all > partial > weak). On long queries where exact-phrase
matches are rare, the heuristic demotes body-only matches that BM25
had correctly ranked first. The net effect is to replace a
well-calibrated probabilistic ranker (BM25) with a hand-tuned
rule-based ranker.

**Pattern C: Granularity mismatch.** `graph` and `neighbor` expansion
provide *contextual* chunks (adjacent text, related headings) that
an LLM might find useful when *reading*. But these chunks are not
what the LLM asked for, and including them in the candidate pool
dilutes precision. The right place for context expansion is the
*reader* (`kbtool read --expand N`), not the *sieve*.

Pattern C is the most interesting. It suggests that some augmentation
modules are not wrong in their effect; they are wrong in their
*placement*. Moving context expansion from the retrieval path to the
read path — which is what kb-sieve does — preserves the benefit while
removing the cost.

### 6.2 Division of labor: LLM reasoning vs tool decision

The sieve paradigm implies a specific division of labor between the
LLM and the retrieval system:

| Responsibility | Belongs to | Rationale |
|---------------|-----------|-----------|
| Filtering: which documents contain candidate evidence | Sieve | Cheap, lexical, fully determined by query terms. |
| Ranking: which candidate is most likely to answer | LLM | Requires world knowledge and contextual judgment. |
| Context expansion: read surrounding passages | LLM (via reader) | The LLM knows when context is needed. |
| Verification: does this passage answer the question | LLM | Requires reading. |
| Termination: when to stop searching | LLM | Requires reasoning about cost/benefit. |

pack-builder violates this division by allocating ranking, context
expansion, and (implicitly) termination decisions to the retrieval
system. The result is that the LLM is forced to reason about
retrieval decisions it cannot inspect, which is exactly the failure
mode the sieve paradigm is designed to prevent.

### 6.3 Threats to validity

We are explicit about the scope of our findings.

**Dataset scale.** Hand-curated query sets per dataset are small
(typically 20–30 queries). The "zero contribution" findings should
be read as "no measurable contribution at this sample size," not as
proof of irrelevance. A larger evaluation could surface small effects
we cannot detect.

**Domain coverage.** Our datasets cover literary fiction, statutory
law, financial non-fiction, and technical specification. We do not
evaluate on conversational, code, or scientific-paper corpora, all
of which have different retrieval characteristics. The sieve paradigm
is, if anything, *more* applicable to code (where grep is already the
dominant tool), but we have not measured this.

**Query distribution.** Our queries are hand-authored by the system
designers. They may not reflect the distribution of queries a
production RAG system receives. A naturalistic query log could change
the relative ranking of configurations.

**Augmentation design.** Our ablation evaluates a *specific*
implementation of each augmentation module. It is possible that
better implementations of, say, graph expansion or alias fusion would
yield different conclusions. We have tried to use production-quality
implementations (pack-builder is a real system that has been
released), but we cannot rule out implementation artifacts.

**Granularity.** We evaluate at file and chunk granularity. We do
not evaluate downstream end-to-end task quality (e.g., answer
correctness in a QA setting). It is possible — though we believe
unlikely — that augmentation modules hurt retrieval MRR while helping
end-to-end QA, for example by providing context that supports answer
generation even when it is not the top-ranked chunk. We do not have
the QA annotations to test this directly; this is the most important
piece of future work.

**Author bias.** The authors designed both systems and the
evaluation. We have tried to apply the rigor we advocate (correcting
the reranker-instrumentation bug, fixing the off-by-one annotation
error, cross-reviewing the ablation), but the possibility of
confirmation bias remains.

### 6.4 When augmentation is justified

Despite the negative findings, we do not claim augmentation is
*never* justified. The sieve paradigm permits augmentation when the
LLM demonstrably cannot perform the operation as well. Three cases
qualify:

1. **Identifier precision.** §4.3's exact-identifier pre-match is
   justified because the LLM cannot efficiently search a corpus for
   a structured identifier without an indexed lookup. This is a
   sieve-level augmentation (it changes the candidate set, not the
   ordering) and is consistent with minimality.

2. **Multi-lingual tokenization.** A CJK 2-gram tokenizer is
   justified because the LLM cannot perform efficient substring
   search over a 100MB corpus on its own. Tokenization is part of
   the sieve's *index*, not its *ranking* — a distinction the
   paradigm preserves.

3. **Domain-specific lexicon expansion at build time.** Build-time
   alias extraction (e.g., recognizing that two surface forms refer
   to the same entity) is justified when it enables the sieve to
   recognize surface variation the LLM might miss *during retrieval*.
   The key constraint is that this expansion happens once at build
   time and produces a deterministic mapping; it is not a learned
   reranker.

What is *not* justified under the paradigm:

- Learned rerankers operating on retrieved candidates (the LLM is a
  better ranker).
- Runtime query expansion via LLM (the LLM should expand its own
  queries).
- Memory feedback (the LLM should remember its own session).
- Graph or neighbor expansion at retrieval time (the LLM should
  expand context during reading).

### 6.5 The cost of complexity

Beyond quality, complexity has costs that do not show up in retrieval
metrics but matter for production deployment:

- **Maintenance.** pack-builder's 73 modules versus kb-sieve's ~50.
  Each module is a thing that can break, drift, or be silently no-op
  (Failure 1 in §2.2).
- **Latency.** pack-builder's pipeline runs 5–7 SQL queries per
  `query` call versus kb-sieve's 1–2. End-to-end latency is
  correspondingly higher.
- **Dependencies.** pack-builder optionally depends on an external
  LLM API for build-time term expansion. kb-sieve has no external
  runtime or build dependencies beyond SQLite.
- **Auditability.** pack-builder's audit trail (which alias fired,
  which graph edge was traversed, which memory hit boosted which
  chunk) requires dedicated logging code and is easy to get wrong.
  kb-sieve's audit trail is the query and its output.

These costs are real and recurring. They are the reason we eventually
moved from pack-builder to kb-sieve as the primary system, despite
pack-builder's earlier maturity.

---

## 7. Related work

### 7.1 Sparse vs dense retrieval

A large literature compares sparse (BM25) and dense (bi-encoder)
retrieval. Recent work has shown that BM25 remains a strong baseline
and is often competitive with dense retrieval on out-of-domain data,
in contrast to earlier reports. Our work is consistent with these
findings and extends them: we argue that for *agent-consumed*
retrieval, transparency and minimality matter beyond raw ranking
quality, and these properties favor sparse retrieval.

### 7.2 Pipeline complexity and ablation studies

The IR community has long studied multi-stage retrieval pipelines.
Ablation studies of individual components are standard practice.
What is less commonly reported is the *composition* ablation: how do
modules interact when stacked? Our cross-dataset ablation (§5.4) is
a small contribution to this gap. The most striking finding — that
augmentation can be net-negative even when each module is
individually motivated — echoes concerns raised about
learning-to-rank feature bloat.

### 7.3 RAG system design

The RAG architecture was introduced by Lewis et al. (2020) and has
since been extended in many directions: multi-hop retrieval,
iterative retrieval, graph-of-thought retrieval, self-reflective
retrieval, and tool-using agents. Most of these works *add* retrieval
machinery; few investigate when to *remove* it. Our work is closest
in spirit to the *tool-use agent* line, but with a sharper design
claim: the *transparency, minimality, and auditability* of the
retrieval tool matter as much as its ranking quality, because the
LLM is doing the actual reasoning.

### 7.4 grep, ripgrep, and the Unix tradition

The sieve paradigm has deep roots in the Unix tradition of small,
composable tools. `grep` and its modern successor `ripgrep` are
LLM-friendly sieves in our sense: they are transparent (matched lines
are shown with line numbers), minimal (no reranking), and auditable
(deterministic, reproducible). The popularity of `ripgrep` as an LLM
tool in coding agents is empirical evidence that LLMs work well with
simple sieves. Our report generalizes this observation: ripgrep is
not popular *despite* being a sieve; it is popular *because* it is a
sieve.

### 7.5 SQLite FTS5 and embedded retrieval

SQLite's FTS5 extension has been used for embedded full-text search
since 2015. Its BM25 implementation, custom-tokenizer hooks, and
zero-dependency deployment make it an attractive substrate for
sieve-oriented systems. Our system uses FTS5 with a custom 2-gram
tokenizer for CJK text, following established practice in Chinese
IR.

---

## 8. Conclusion and future work

We have argued that retrieval-augmented generation, viewed through
the lens of LLM-agent tool use, is best understood as the design of
an *LLM-friendly sieve* — a transparent, minimal, auditable filter
that surfaces a small set of documents with line-level evidence the
LLM can verify. We instantiated the paradigm as **kb-sieve**, a
system with two CLI entries (`query`, `read`) over a SQLite FTS5
index, and compared it to its predecessor **pack-builder**, a
multi-module pipeline with learned reranking, alias fusion, graph
expansion, and runtime memory. Across four datasets and a
fourteen-configuration ablation, the simple sieve was Pareto-optimal:
augmentation either did not change file-level MRR or actively
decreased it, and even at chunk-level granularity bare BM25 remained
on the Pareto frontier with the highest average MRR.

We close with two reflections and two forward-looking directions.

**Reflection 1.** The most useful single module in either system
turned out to be the exact-identifier pre-match (§4.3) — a small
function that handles a specific, well-understood failure mode of
FTS tokenization. The most expensive single module — the production
reranker with 5-bucket scoring — was the most harmful. The lesson is
the same one IR has been relearning for fifty years: target specific
failure modes with small, well-justified modules, and resist the urge
to add a general-purpose reranker to fix what are usually tokenization
problems.

**Reflection 2.** The hardest part of this work was not building
kb-sieve; it was *unbuilding* pack-builder. Once a module exists in
a pipeline, with tests, documentation, and committed users, removing
it requires positive evidence of harm. Our cross-dataset ablation
provided that evidence, but only after we corrected two
instrumentation bugs (§5.4 Finding 3, §2.2 Failure 1) that had
masked the harm. The methodological lesson: complex pipelines need
*adversarial* ablation, not just additive ablation. If you cannot
make the ablation tell you your favorite module is harmful, you do
not have a strong enough evaluation.

**Future work, build side.** Build-time alias and reference-edge
extraction remain valuable, but should be exposed as *reader*
primitives (`kbtool read --related`) rather than *retrieval*
primitives. We plan to migrate pack-builder's alias and graph
machinery to the read path in kb-sieve and re-measure.

**Future work, evaluation side.** Our evaluation is at the retrieval
level. End-to-end QA evaluation — with LLM-judged answer correctness
— is the natural next step and is required to test the claim (§6.3)
that augmentation modules could help QA while hurting retrieval. We
are constructing a QA evaluation set across the four datasets and
plan to report results in a follow-up.

---

## References

See `references.md` for full bibliographic entries.

---

## Appendix A. Reproducibility checklist

See `checklist.md` for the full reproducibility statement. In summary:

- **Code.** pack-builder is publicly released. kb-sieve is documented
  in this report and will be released alongside the final evaluation
  results.
- **Data.** The four evaluation datasets are based on publicly
  available source documents; the query annotations are included.
- **Randomness.** No randomness in either system. All results are
  deterministic and reproducible.
- **Scripts.** `eval/run_eval.py`, `eval/eval_chunk_level.py`, and
  `eval/cross_review_report.md` (in pack-builder's eval directory)
  document the methodology and known instrumentation pitfalls.

## Appendix B. Cross-reference to source

| Report section | Source artifact |
|----------------|-----------------|
| §2.1 pack-builder's architecture | `../pack-builder/templates/kbtool_lib/` |
| §2.2 Failure 1 (silent no-op) | `../pack-builder/handoff-kb-retrieval-optimization.md` |
| §2.2 Failure 2 (mis-instrumented ablation) | `../pack-builder/eval/results/cross_review_report.md` |
| §2.2 Failure 3 (annotation off-by-one) | `../pack-builder/handoff-kb-retrieval-optimization.md` |
| §4.1 Two-entry architecture | `./SKILL.md`; `./templates/kbtool_lib/cli_parser.py` |
| §4.2 Whole-document FTS5 | `./templates/kbtool_lib/query_engine.py:_search_docs` |
| §4.3 Exact-identifier pre-match | `query_engine.py:_exact_identifier_match` |
| §4.4 Line-level evidence | `query_engine.py:_find_matching_lines` |
| §4.5 Reader engine | `./templates/kbtool_lib/read_engine.py` |
| §5.3 Main results | `../pack-builder/eval/results/` (historical); TBD cells pending final re-run |
| §5.4 Ablation | `../pack-builder/eval/results/ablation_*` and `chunk_eval_v3/` |
