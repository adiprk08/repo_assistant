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

- **Judge model:** claude-opus-4-8, rubric-scored per dimension (1–5): correctness, groundedness (claims supported by cited spans), completeness, and honesty-on-negatives. Judge sees question, answer, cited spans, and gold evidence — not the retrieval internals.
- **Citation metrics are mechanical, not judged:** citation precision = fraction of emitted citations that verify against the index AND are judged relevant; citation recall = fraction of gold evidence spans covered.
- Judge calibration: a 30-item human-graded anchor set; judge/human agreement (Cohen's κ) re-checked whenever the judge prompt or model changes.

## 4. Execution and gates

- `ra eval run [--suite smoke|full] [--repo ...]` — results to Postgres (`eval_runs`, `eval_results`) with the config snapshot (embedder, chunker params, model IDs, prompt hashes) for every run, so any two runs are diffable.
- **CI (per PR touching retrieval/reasoning/chunking):** smoke suite (1 small repo, ~40 questions, retrieval metrics + citation validity only — no judge, keeps it fast/cheap). Gate: retrieval recall@10 and citation validity may not drop > 2 points vs. main.
- **Nightly:** full suite including judge grading; trend dashboard; regressions open issues automatically.
- **Ablations on demand:** the harness accepts config overrides (e.g. `--no-rerank`, `--dense-only`, `--no-context-headers`) so every architecture claim in the ADRs stays empirically backed.

## 5. Phase 1 baseline (recorded)

First recorded baseline — **2026-07-10**, `claude-opus-4-8` + `voyage-code-3` (1024-d), **dense-only** retrieval, 16 questions over two small JS repos (`is-plain-obj`, `yocto-queue`). This is the reference every Phase 2 retrieval change is measured against.

| Metric | Overall |
|---|---|
| Retrieval recall@12 | 1.00 |
| Answer correctness (judge, 1–5) | 4.83 |
| Groundedness (judge, 1–5) | 4.42 |
| Citation presence | 1.00 |
| Citation file precision | 0.92 |
| Negative handled rate | 1.00 |
| **Pass rate** | **1.00** (16/16) |

Caveats: small starter set on tiny single-file repos — retrieval recall is near-trivially high here because each repo has one obvious evidence file; the number will become discriminating once the benchmark set includes medium/large multi-file repos (gated on a Voyage payment method lifting the free-tier rate limit). The value of this run was less the scores than catching the negative-handling metric flaw above.

## 6. Cost discipline

Every eval run records token spend. Full-suite cost is itself a tracked metric — an eval too expensive to run nightly stops being run, so suite size and judge usage are budgeted (smoke ≈ $0 LLM spend beyond embeddings; nightly full suite budget set in Phase 2 and enforced).
