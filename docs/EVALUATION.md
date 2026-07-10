# Evaluation methodology

> Status: designed (2026-07); harness lands in Phase 2 with a starter set in Phase 1. Principle: **no retrieval or prompting change merges without numbers.**

## 1. What we measure, separately

RAG failures compound; measuring only end-to-end answers hides whether retrieval or generation regressed. We evaluate three layers independently plus end-to-end:

| Layer | Question | Metrics |
|---|---|---|
| Retrieval | Did the right spans reach the candidate set? | recall@k (k=5,10,25), MRR, nDCG@10 vs. labeled relevant files/spans |
| Routing | Did the query take the right path? | router accuracy vs. labeled intent; agent tool-budget adherence |
| Generation | Given good context, is the answer right and grounded? | LLM-judge rubric (below); citation precision/recall |
| End-to-end | Does the user get a correct, cited answer? | judge grade × citation validity; latency + cost per query |

## 2. Datasets

**Benchmark repos** — fixed at pinned commits so results are reproducible:

- 5–8 public repos spanning: small (<300 files) / medium (~2k) / large (10k+); Python, TypeScript, Go, mixed; app / library / monorepo shapes. Selected in Phase 1; pinned SHAs recorded in `evals/repos.yaml`.

**Golden set** — per repo, hand-authored Q&A with labeled evidence (relevant file paths + spans), covering the question taxonomy:

| Category | Example |
|---|---|
| `lookup` | "Where is the retry backoff configured?" |
| `explain` | "What does `SessionManager.refresh()` do?" |
| `architecture` | "How is the plugin system structured?" |
| `trace` | "What happens from HTTP request to DB write when a user signs up?" |
| `debug` | "Why might `parse_config` raise KeyError on older config files?" |
| `negative` | Questions whose true answer is "not present in this repo" — rewards honest handling |

> **Negative handling, not just refusal (learned in the Phase 1 baseline).** Dense
> retrieval always returns *some* chunks, so the empty-retrieval refusal path rarely
> fires for negatives; instead the model gives a grounded "this capability isn't in
> the repo, it only does X" answer — which is correct, often better than a blank
> refusal. The metric therefore *judges* whether a negative was correctly handled
> (absence indicated without fabrication), rather than string-matching a refusal.

**Synthetic expansion** — LLM-generated questions from sampled symbols/files (generation prompt includes the evidence, so labels are free); a human-verified subset guards against generator drift. Target: ~50 golden + ~200 synthetic per benchmark repo.

## 3. Judging

- **Judge model:** claude-opus-4-8, rubric-scored per dimension (1–5): correctness, groundedness (claims supported by cited spans), completeness, and honesty-on-negatives. Judge sees question, answer, gold-evidence file names, and the **retrieved source excerpts the answer was grounded in** (the assembled generation context, capped for token cost) — not the retrieval internals. The excerpts are the authoritative ground truth: the judge grades correctness against them rather than its own memory of the library, which fixed a false negative where a correct answer describing recent (click 8.2) APIs was scored wrong because the judge's parametric knowledge was stale (see §5, task 24).
- **Citation metrics are mechanical, not judged:** citation precision = fraction of emitted citations that verify against the index AND are judged relevant; citation recall = fraction of gold evidence spans covered.
- Judge calibration: a 30-item human-graded anchor set; judge/human agreement (Cohen's κ) re-checked whenever the judge prompt or model changes.

## 4. Execution and gates

- `ra eval run [--suite smoke|full] [--repo ...]` — results to Postgres (`eval_runs`, `eval_results`) with the config snapshot (embedder, chunker params, model IDs, prompt hashes) for every run, so any two runs are diffable.
- **CI (per PR touching retrieval/chunking/indexing):** `ra eval --retrieval-only --gate` indexes the benchmark repos and enforces regression floors (recall@10 ≥ 0.90, MRR ≥ 0.70, nDCG@10 ≥ 0.70) — no judge, so the only cost is Voyage embeddings. Implemented in `.github/workflows/retrieval-eval.yml` (requires the `RA_VOYAGE_API_KEY` repo secret; skipped for fork PRs).
- **Nightly:** full suite including judge grading; trend dashboard; regressions open issues automatically.
- **Ablations on demand:** the harness accepts config overrides (e.g. `--no-rerank`, `--dense-only`, `--no-context-headers`) so every architecture claim in the ADRs stays empirically backed.

## 5. Recorded baselines

### Dense-only reference (Phase 2 start) — **2026-07-10**

`claude-opus-4-8` + `voyage-code-3` (1024-d), **dense-only** retrieval, span-level
metrics, **26 questions** over three repos (`click` medium Python, `is-plain-obj`,
`yocto-queue`). **This is the reference every Phase 2 retrieval change is measured against.**

| Metric | Overall |
|---|---|
| recall@5 / @10 / @25 | 0.90 / 1.00 / 1.00 |
| **MRR** | **0.65** |
| **nDCG@10** | **0.67** |
| Answer correctness (judge, 1–5) | 4.70 |
| Groundedness (judge, 1–5) | 4.50 |
| Citation file precision | 0.95 |
| Negative handled rate | 1.00 |
| Pass rate | 0.96 (25/26) |

**Reading:** recall is near-saturated — the right evidence almost always lands *somewhere*
in the candidate set — so **MRR/nDCG (ranking quality) are the discriminating metrics**, and
they have clear headroom. `yocto-queue` (MRR 0.27) is the sharpest example: a single file of
small methods where dense embeddings can't distinguish which method chunk is most relevant.
Phase 2's symbol channel, hybrid BM25, and reranking all target getting the right span to
rank 1 — so the target is **MRR/nDCG up**, not recall (already ~1.0).

### Symbol channel added (task 17) — same 26 questions

Hybrid = dense + trigram symbol channel, RRF-fused. Measured against the dense-only
reference above (ablation: `ra eval --dense-only` vs `ra eval`).

| Metric (overall) | Dense-only | Hybrid | Δ |
|---|---|---|---|
| recall@5 | 0.90 | 1.00 | +0.10 |
| MRR | 0.65 | **0.82** | **+0.17** |
| nDCG@10 | 0.67 | **0.84** | **+0.17** |
| Pass rate | 0.96 | 1.00 | +0.04 |

Meets the Phase 2 ≥15-point ranking target on the symbol channel alone. Per-repo MRR:
`yocto-queue` 0.27→1.00 (exact matches on `enqueue`/`drain` nail method questions),
`is-plain-obj` 0.64→0.81, but **`click` 0.94→0.69 regressed**: with 1,886 symbols and many
homonyms (a dozen `convert` methods, multiple `invoke`s), an identifier query floods the
symbol channel with equally-scored matches that RRF lets crowd out the relevant dense hit.
The channel trades precision for recall on large repos — the motivation for cross-encoder
reranking (task 19), which restores order over the fused candidates.

### Code-graph channel added (task 22) — retrieval-only ablation

The graph channel (contains + heuristic call edges, 1-hop neighbor expansion) is
**neutral on the current benchmark** (MRR 0.89→0.89, nDCG 0.77→0.74) because those
questions are explain/lookup, not trace. It is therefore **off by default**
(opt-in `ra eval --graph`) pending its fair test — the trace/architecture question
sets (task 24), which name a symbol and expect its *callers/callees* as evidence.
Same discipline as reranking: no channel ships on without measured benefit.
**Verdict rendered below (task 24): the fair test confirms off-by-default.**

### Trace/architecture question sets + graph-channel verdict (task 24)

Dataset expanded **26 → 36 questions**: 7 new `trace` + 3 new `architecture` for
`click`, 1 new `trace` for `yocto-queue` — multi-span, mostly cross-file
caller-callee evidence (e.g. `main()` → `Command.invoke` → `Context.invoke`;
`consume_value` → `prompt_for_value` → `termui.prompt`), span-labeled from a
fresh clone of the pinned commits. Final mix: 9 trace / 6 architecture / 9
explain / 6 lookup / 6 negative. The harness now reports **per-category
metrics** (`by_category` in reports and CLI) — the unit channel ablations are
judged on.

**New retrieval baseline** (dense+sparse+symbol, retrieval-only, 36 Q — not
comparable to the 26-Q numbers above; the dataset changed):
overall MRR 0.92 / nDCG@10 0.77 / recall@5 0.92 / recall@10 0.93. CI gate floors
(recall@10 ≥ 0.90, MRR ≥ 0.70, nDCG@10 ≥ 0.70) still clear on the expanded set.
On multi-span questions **MRR saturates** (a symbol-channel hit lands rank 1
almost always), so **span coverage (recall@k) and nDCG@10 are the discriminating
metrics** for trace/architecture.

**Graph channel A/B** (`ra eval --retrieval-only` vs `--retrieval-only --graph`),
per category:

| Metric | no graph | +graph | Δ |
|---|---|---|---|
| trace nDCG@10 | **0.76** | 0.66 | **−0.10** |
| trace recall@5 / @10 | 0.81 / 0.85 | 0.81 / 0.85 | 0 / 0 |
| architecture nDCG@10 | **0.85** | 0.80 | **−0.05** |
| architecture MRR | **1.00** | 0.92 | −0.08 |
| architecture recall@10 / @25 | 0.89 / 0.94 | **1.00 / 1.00** | **+0.11 / +0.06** |
| overall MRR / nDCG@10 | 0.92 / 0.77 | 0.93 / 0.73 | +0.01 / −0.04 |

**Verdict: off by default stands, now on a fair test.** The channel behaves
exactly as ADR-0005 predicted — a *recall* device: it rescued the one question
dense+sparse missed outright (`clk-arch-parser-1` recall@10 0.33→1.00) and finds
deep spans (`clk-trace-main-1` recall@25 0.67→1.00). But it pays for that in
ranking quality on the very categories it targets. **Failure mode: hub-symbol
flooding.** Trace questions name high-degree symbols (`invoke`: 2,376 edges,
`cli`: 15,088 in click's graph); 1-hop expansion around a hub injects a large,
weakly-ordered neighbor set into RRF, displacing labeled evidence
(`clk-trace-main-1` nDCG 0.76→0.42; `clk-trace-runner-1` recall@25 1.00→0.50;
`clk-parser-1` MRR 1.00→0.50). Decision + candidate fixes (degree-capped
expansion, down-weighted fusion) recorded in **ADR-0011**; the graph's primary
consumer remains targeted traversal by the Phase 3 agent loop (task 25), not
blind channel fusion.

### Phase 3 full judged baseline — 36-question set (task 24)

Full run (generation + judge) on the expanded 36-question set, default config
(dense+sparse+symbol, no graph, no rerank), `claude-opus-4-8` generation + judge
(evidence-aware judging, see below):

| Metric | Overall | trace | architecture |
|---|---|---|---|
| **Pass rate** | **1.00 (36/36)** | 1.00 | 1.00 |
| Answer correctness (1–5) | 4.97 | — | — |
| Groundedness (1–5) | 4.77 | — | — |
| Citation presence | 1.00 | — | — |
| Citation file precision | 0.97 | — | — |
| Negative handled rate | 1.00 | — | — |
| MRR / nDCG@10 | 0.92 / 0.77 | 1.00 / 0.76 | 1.00 / 0.85 |

**Two judge bugs found and fixed getting to this clean baseline** — both surfaced
by `clk-test-1` ("How does CliRunner invoke a command in isolation for testing?"),
which retrieved correctly (recall@5 1.00, 13 citations) throughout:

1. *Unparseable-output false negative.* On one call the judge returned prose with
   no JSON; the fallback hard-coded `correctness=1`, indistinguishable from a
   wrong answer and corrupting pass_rate. **Fix:** retry once on unparseable
   output, token headroom (`_JUDGE_MAX_TOKENS=512`), reject non-object JSON.
2. *Stale-knowledge false negative.* With parsing fixed, the judge scored the
   (correct) answer 2/2, calling `_FDCapture`, the `capture="sys"/"fd"` param, and
   the three-stream BytesIO output "fabricated" — all of which **do** exist in
   `testing.py` at the pinned commit (verified against source). Root cause: the
   judge graded correctness from its own memory of click, never seeing the code
   (the implementation passed only gold-evidence *file names*, contradicting the
   §3 design). **Fix:** feed the judge the retrieved source excerpts the answer
   was grounded in and instruct it to treat them as authoritative over prior
   knowledge. `clk-test-1` → correctness 5; overall correctness 4.73→4.97,
   groundedness 4.53→4.77 as the judge stopped second-guessing correct answers.

Both fixes are covered by `tests/unit/test_judge.py`. This is a worked example of
the eval catching a *judge* defect, not just a system defect — the discipline
cuts both ways.

### Phase 2 full baseline — dense+sparse+symbol (best config)

Full run (generation + judge) on the 26-question set, `claude-opus-4-8` judge:

| Metric | Overall |
|---|---|
| recall@5 / MRR / nDCG@10 | 1.00 / 0.86 / 0.87 |
| Answer correctness (1–5) | 4.70 |
| Groundedness (1–5) | 4.50 |
| Citation presence | 1.00 |
| Citation file precision | 1.00 (was 0.92 dense-only) |
| Negative handled rate | 1.00 |
| **Pass rate** | **1.00 (26/26)** |

The retrieval gains flowed through to grounding — citation file precision rose to 1.00.
Runs are persisted to `eval_runs`/`eval_results`; `ra eval --retrieval-only --gate`
enforces the regression floors in CI (docs §4).

### Sparse BM25 channel added (task 18) — retrieval-only ablation

Adding a BM25 sparse channel (Qdrant IDF-modifier sparse vectors, dependency-free
code-aware tokenizer) as a third RRF channel:

| Metric (overall) | dense | dense+symbol | dense+sparse+symbol |
|---|---|---|---|
| recall@5 | 0.90 | 1.00 | 1.00 |
| MRR | 0.65 | 0.79 | **0.86** |
| nDCG@10 | 0.67 | 0.82 | **0.87** |

Sparse lifts MRR +0.07 / nDCG +0.05 over dense+symbol and **recovers `click`** (MRR
0.62→0.78): the lexical signal complements the noisy symbol channel on the large,
homonym-heavy repo. **Current best config: dense + sparse + symbol, RRF-fused, no
rerank — MRR 0.86 / nDCG 0.87 (+21 / +20 over the dense baseline).** (`click`'s own
dense-only MRR 0.94 still edges the hybrid, so a repo where dense already nails it
gains slightly from extra channels — a candidate for adaptive channel weighting later.)

### Reranking evaluated and rejected (task 19) — retrieval-only ablation

Three-way ablation on the 26-question set, retrieval metrics only (no LLM cost):

| Metric (overall) | dense | dense+symbol | +rerank (rerank-2.5) |
|---|---|---|---|
| recall@5 | 0.90 | **1.00** | 0.95 |
| MRR | 0.65 | **0.82** | 0.67 |
| nDCG@10 | 0.67 | **0.84** | 0.71 |

**Cross-encoder reranking made ranking worse** and is **disabled by default** (opt-in via
`ra eval --rerank`). Per-repo MRR: yocto-queue 1.00→0.81, click 0.69→0.56 — the reranker
*demotes* the exact symbol matches RRF correctly ranks first. A general-purpose cross-encoder
scores prose relevance, so for an identifier query ("how does `enqueue` work") it can rank a
chunk that *describes* enqueuing above the actual `enqueue` function. See ADR-0010. Context
assembly (overlap dedup + per-file cap) is kept — it improves citation quality, independent of
ranking, so it isn't captured by these retrieval-only numbers.

**Current best config: dense + symbol, RRF-fused, no rerank — MRR 0.82 / nDCG 0.84** (+17 pts
over the dense baseline; meets the Phase 2 target).

### Phase 1 starter (superseded) — 16 questions, 2 tiny JS repos

Pass rate 1.00, correctness 4.83. Recall was near-trivially 1.0 (one obvious evidence file
per repo); its real value was catching the negative-handling metric flaw (§2). Superseded by
the expanded span-level baseline above.

## 6. Cost discipline

Every eval run records token spend. Full-suite cost is itself a tracked metric — an eval too expensive to run nightly stops being run, so suite size and judge usage are budgeted (smoke ≈ $0 LLM spend beyond embeddings; nightly full suite budget set in Phase 2 and enforced).
