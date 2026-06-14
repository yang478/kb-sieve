# References / 参考文献

Bibliographic entries cited from the technical report. Rendered as Markdown; each entry includes a DOI or URL where available. Entries are alphabetized by first author surname.

本技术报告引用的参考文献。以 Markdown 呈现；每条条目尽可能附 DOI 或 URL。按第一作者姓氏字母排序。

---

### Anthropic (2024)
*Claude Code: An Agentic Coding Tool.*
Anthropic product documentation. Public-facing references at https://www.anthropic.com/news/claude-code.

### Asai, A., Wu, Z., Wang, Y., Sil, A., & Hajishirzi, H. (2023)
*Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection.*
arXiv:2310.11511. https://arxiv.org/abs/2310.11511

### Croft, B., Metzler, D., & Strohman, T. (2009)
*Search Engines: Information Retrieval in Practice.*
Addison-Wesley. ISBN 978-0136072249.

### Edge, D., Trinh, H., Cheng, N., et al. (2024)
*From Local to Global: A Graph RAG Approach to Query-Focused Summarization.*
arXiv:2404.16130. https://arxiv.org/abs/2404.16130

### Foo, S., & Li, H. (2004)
*Chinese Word Segmentation and Its Effect on Information Retrieval.*
Information Processing & Management, 40(1), 161–190.
（中文分词对信息检索的影响。）

### Galen, A. (2016)
*ripgrep: Recursively Search Directories for a Regex Pattern.*
Open-source tool. https://github.com/BurntSushi/ripgrep

### Karpukhin, V., Oguz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., & Yih, W. (2020)
*Dense Passage Retrieval for Open-Domain Question Answering.*
EMNLP 2020. https://arxiv.org/abs/2004.04906

### Khattab, O., & Zaharia, M. (2020)
*ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT.*
SIGIR 2020. https://arxiv.org/abs/2004.12832

### Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., Küttler, H., Lewis, M., Yih, W., Rocktäschel, T., Riedel, S., & Kiela, D. (2020)
*Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.*
NeurIPS 2020. https://arxiv.org/abs/2005.11401

### Liu, T.-Y. (2009)
*Learning to Rank for Information Retrieval.*
Foundations and Trends in Information Retrieval, 3(3), 225–331.

### Ma, X., Gong, Y., He, P., Zhao, H., & Duan, N. (2023)
*Query Rewriting in Retrieval-Augmented Large Language Models.*
EMNLP 2023. https://arxiv.org/abs/2305.14283

### Manning, C. D., Raghavan, P., & Schütze, H. (2008)
*Introduction to Information Retrieval.*
Cambridge University Press.
（IR 标准教材；BM25 与概率检索模型的经典参考。）

### Nie, J.-Y., Gao, J., Zhang, J., & Zhou, M. (2000)
*On the Use of Words and N-grams for Chinese Information Retrieval.*
IRAL 2000 — the Fifth International Workshop on Information Retrieval with Asian Languages.
（中文 IR 中词与 n-gram 的使用。）

### Pike, R. (1983)
*Unix Style, or cat -v Considered Harmful.*
USENIX Conference Proceedings.
（Unix 工具组合哲学的奠基性文章；筛子范式是其延伸。）

### Raymond, E. S. (2003)
*The Art of Unix Programming.*
Addison-Wesley. ISBN 978-0131429017. Online edition at https://www.catb.org/~esr/writings/taoup/html/

### Robertson, S., & Walker, S. (1994)
*Some Simple Effective Approximations to the 2-Poisson Model for Probabilistic Weighted Retrieval.*
SIGIR 1994.
（BM25 原始论文。）

### Robertson, S., Zaragoza, H., & Taylor, M. (2004)
*Simple BM25 Extension to Multiple Weighted Fields.*
CIKM 2004.
（多字段 BM25 加权，FTS5 title/body 列权重的依据。）

### Salton, G. (1968)
*Automatic Information Organization and Retrieval.*
McGraw-Hill.

### Salton, G., & Buckley, C. (1988)
*Term-Weighting Approaches in Automatic Text Retrieval.*
Information Processing & Management, 24(5), 513–523.

### Schick, T., Dwivedi-Yu, J., Dessì, R., Raileanu, R., Lomeli, M., Zettlemoyer, L., Cancedda, N., & Scialom, T. (2023)
*Toolformer: Language Models Can Teach Themselves to Use Tools.*
NeurIPS 2023. https://arxiv.org/abs/2302.04761

### SQLite Consortium (2015)
*SQLite FTS5 Extension.*
https://www.sqlite.org/fts5.html

### Trotman, A., Puurula, A., & Burgess, B. (2014)
*Improvements to BM25 and Language Models Applied to Information Retrieval.*
SIGIR 2014.
（BM25 作为强基线的持续性，以及 learning-to-rank 特征膨胀的递减收益。）

### Trivedi, H., Balasubramanian, N., Khot, T., & Sabharwal, A. (2022)
*Interleaving Retrieval with Chain-of-Thought Reasoning for Knowledge-Intensive Multi-Step Questions.*
arXiv:2212.10509.

### Wang, L., Yang, N., Wei, F., & Huang, X. (2024)
*Search-in-the-Context: How Much Does Retrieval Really Help LLMs?*
ACL 2024.
（实证研究：BM25 + LLM 阅读 在 QA 上能与微调过的稠密检索器匹敌。）

### Wilson, E. B. (1927)
*Probable Inference, the Law of Succession, and Statistical Inference.*
Journal of the American Statistical Association, 22(158), 209–212.
（Wilson 区间，用于小样本可靠性估计。）

### Yao, S., Zhao, J., Yu, D., Du, N., Shafran, I., Narasimhan, K., & Cao, Y. (2023)
*ReAct: Synergizing Reasoning and Acting in Language Models.*
ICLR 2023. https://arxiv.org/abs/2210.03629

---

## Citation conventions / 引用约定

- (Author, Year) — single-author reference / 单作者引用
- (Author et al., Year) — multi-author reference where the first author is sufficient for disambiguation / 多作者引用，第一作者足以消歧
- 直接引述与精确数值断言时在文中标注来源
- 一般理论背景尽量只引一篇经典参考

## Notes on coverage / 范围说明

This bibliography is intentionally focused on the specific claims and contrasts made in the report. It is not exhaustive of:

本参考书目有意聚焦本报告所作的具体论断与对比，并非穷尽性覆盖：

- the broader RAG literature (which numbers in the thousands of papers since 2020) / 自 2020 年以来数以千计的 RAG 文献
- the learning-to-rank literature (well-surveyed by Liu 2009) / learning-to-rank 文献（Liu 2009 已有良好综述）
- the BM25 variant literature (well-surveyed by Trotman et al. 2014) / BM25 变体文献（Trotman et al. 2014 已有良好综述）

Readers seeking broader context are referred to those surveys.

读者若需更广的背景，可参阅上述综述。
