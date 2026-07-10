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

### Phase 1 starter (superseded) — 16 questions, 2 tiny JS repos

Pass rate 1.00, correctness 4.83. Recall was near-trivially 1.0 (one obvious evidence file
per repo); its real value was catching the negative-handling metric flaw (§2). Superseded by
the expanded span-level baseline above.

## 6. Cost discipline

Every eval run records token spend. Full-suite cost is itself a tracked metric — an eval too expensive to run nightly stops being run, so suite size and judge usage are budgeted (smoke ≈ $0 LLM spend beyond embeddings; nightly full suite budget set in Phase 2 and enforced).
