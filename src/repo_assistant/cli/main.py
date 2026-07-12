"""Repo Assistant CLI: index a repository and chat with it (Phase 1)."""

import asyncio

import typer

from repo_assistant import __version__
from repo_assistant.core.errors import NotFoundError, ProviderError
from repo_assistant.reasoning.service import Answer

app = typer.Typer(
    name="ra",
    help="Repo Assistant - a RAG-powered GitHub repository assistant.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the installed repo-assistant version."""
    typer.echo(__version__)


@app.command()
def index(
    github_url: str = typer.Argument(..., help="Public GitHub repository URL."),
    ref: str | None = typer.Option(None, "--ref", help="Branch, tag, or commit to index."),
    enrich: bool = typer.Option(
        False, "--enrich", help="Add LLM contextual descriptions to chunks (Haiku; ADR-0002)."
    ),
) -> None:
    """Clone, parse, and index a GitHub repository."""
    asyncio.run(_index(github_url, ref, enrich))


@app.command()
def chat(
    repo: str = typer.Argument(..., help="Repository URL or id of an already-indexed repo."),
    path: str = typer.Option(
        "auto", "--path", help="Reasoning path: auto (router), fast, or agent."
    ),
) -> None:
    """Start an interactive, cited chat session over an indexed repository."""
    asyncio.run(_chat(repo, path))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
) -> None:
    """Run the FastAPI service (repos, ingestion jobs, search, chat)."""
    import uvicorn

    uvicorn.run(
        "repo_assistant.api.app:create_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
    )


@app.command()
def worker() -> None:
    """Run the arq ingestion worker (needs Redis + the storage stack)."""
    from arq import run_worker

    from repo_assistant.workers.settings import WorkerSettings

    run_worker(WorkerSettings)  # type: ignore[arg-type]


@app.command()
def eval(
    datasets_dir: str = typer.Option("evals/datasets", "--datasets", help="Golden dataset dir."),
    dense_only: bool = typer.Option(
        False, "--dense-only", help="Ablation: dense channel only (no sparse, no symbol)."
    ),
    no_sparse: bool = typer.Option(
        False, "--no-sparse", help="Ablation: disable the BM25 sparse channel."
    ),
    graph: bool = typer.Option(
        False,
        "--graph",
        help="Enable the code-graph channel (off by default; validate on trace questions).",
    ),
    rerank: bool = typer.Option(
        False,
        "--rerank",
        help="Enable cross-encoder reranking (off by default; measured net-negative).",
    ),
    retrieval_only: bool = typer.Option(
        False,
        "--retrieval-only",
        help="Score retrieval metrics only (no generation/judge, no LLM cost).",
    ),
    agentic: bool = typer.Option(
        False,
        "--agentic",
        help="Route each question through the intent router + fast/agent path (ADR-0006).",
    ),
    gate: bool = typer.Option(
        False, "--gate", help="Exit non-zero if overall retrieval drops below the regression floor."
    ),
) -> None:
    """Run the golden evaluation datasets and record a baseline report."""
    from pathlib import Path

    dataset_paths = sorted(Path(datasets_dir).glob("*.yaml"))
    if not dataset_paths:
        raise typer.Exit(code=_fail(f"No datasets found in {datasets_dir}"))
    asyncio.run(
        _eval(
            dataset_paths,
            use_symbols=not dense_only,
            use_sparse=not dense_only and not no_sparse,
            use_graph=not dense_only and graph,
            use_rerank=rerank,
            retrieval_only=retrieval_only,
            agentic=agentic,
            gate=gate,
        )
    )


async def _index(github_url: str, ref: str | None, enrich: bool) -> None:
    from repo_assistant.cli.runtime import build_runtime
    from repo_assistant.indexing.pipeline import index_repository

    runtime = build_runtime()
    enricher = runtime.llm(model=runtime.settings.enrichment_model) if enrich else None
    try:
        typer.echo(f"Indexing {github_url} {'(enriched) ' if enrich else ''}...")
        result = await index_repository(
            github_url,
            embedder=runtime.embedder(),
            vector_index=runtime.vector_index,
            session_factory=runtime.session_factory,
            ref=ref,
            enricher=enricher,
        )
    except ProviderError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    finally:
        await runtime.aclose()

    typer.secho("\nIndexed successfully.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  repo id:  {result.repo_id}")
    typer.echo(f"  commit:   {result.commit_sha[:12]}")
    typer.echo(f"  files:    {result.n_files}")
    typer.echo(f"  chunks:   {result.n_chunks}")
    typer.echo(f"  symbols:  {result.n_symbols}")
    typer.echo(f"\nChat with it:  ra chat {result.repo_id}")


async def _chat(identifier: str, path: str) -> None:
    from typing import cast

    from repo_assistant.cli.runtime import build_runtime, resolve_indexed_repo
    from repo_assistant.reasoning import answer_routed
    from repo_assistant.reasoning.router import Path

    if path not in ("auto", "fast", "agent"):
        raise typer.Exit(code=_fail("--path must be one of: auto, fast, agent"))
    force_path: Path | None = None if path == "auto" else cast(Path, path)
    runtime = build_runtime()
    try:
        try:
            resolved = await resolve_indexed_repo(runtime, identifier)
        except NotFoundError as exc:
            raise typer.Exit(code=_fail(str(exc))) from exc

        embedder = runtime.embedder()
        llm = runtime.llm()
        router_llm = runtime.llm(model=runtime.settings.router_model)
        typer.secho(f"Chatting with {resolved.url} @ {resolved.commit_sha[:12]}", bold=True)
        typer.echo("Ask a question (empty line or Ctrl-C to quit).\n")

        while True:
            try:
                question = typer.prompt("you").strip()
            except (EOFError, KeyboardInterrupt, typer.Abort):
                typer.echo("\nBye.")
                break
            if not question:
                typer.echo("Bye.")
                break
            try:
                routed = await answer_routed(
                    repo_id=str(resolved.repo_id),
                    snapshot_id=str(resolved.snapshot_id),
                    commit=resolved.commit_sha,
                    question=question,
                    embedder=embedder,
                    vector_index=runtime.vector_index,
                    session_factory=runtime.session_factory,
                    llm=llm,
                    router_llm=router_llm,
                    force_path=force_path,
                    budget=runtime.settings.agent_tool_call_budget,
                )
            except ProviderError as exc:
                typer.secho(f"  provider error: {exc}", fg=typer.colors.RED)
                continue
            path_note = f"[{routed.path}"
            if routed.path == "agent":
                path_note += f", {routed.n_tool_calls} tool calls"
                path_note += ", budget hit" if routed.forced_stop else ""
            typer.secho(f"{path_note}]", fg=typer.colors.MAGENTA)
            if routed.answer is not None:
                _print_answer(routed.answer)
    finally:
        await runtime.aclose()


async def _eval(
    dataset_paths: list,
    *,
    use_symbols: bool,
    use_sparse: bool,
    use_graph: bool,
    use_rerank: bool,
    retrieval_only: bool,
    agentic: bool,
    gate: bool,
) -> None:
    from pathlib import Path

    from repo_assistant.cli.runtime import build_runtime
    from repo_assistant.evaluation import DatasetSpec, run_dataset
    from repo_assistant.evaluation.harness import (
        _overall,
        gate_failures,
        persist_report,
        write_report,
    )

    runtime = build_runtime()
    reports = []
    gate_msgs: list[str] = []
    try:
        for path in dataset_paths:
            dataset = DatasetSpec.from_yaml(path)
            typer.echo(f"Evaluating {path.stem} ({len(dataset.questions)} questions) ...")
            try:
                reports.append(
                    await run_dataset(
                        dataset,
                        runtime,
                        use_symbols=use_symbols,
                        use_sparse=use_sparse,
                        use_graph=use_graph,
                        use_rerank=use_rerank,
                        retrieval_only=retrieval_only,
                        agentic=agentic,
                    )
                )
            except (NotFoundError, ProviderError) as exc:
                raise typer.Exit(code=_fail(str(exc))) from exc

        channels = (
            "dense"
            + ("+sparse" if use_sparse else "")
            + ("+symbol" if use_symbols else "")
            + ("+graph" if use_graph else "")
        )
        config = {
            "generation_model": None if retrieval_only else runtime.settings.generation_model,
            "router_model": runtime.settings.router_model if agentic else None,
            "embedding_model": runtime.settings.embedding_model,
            "embedding_dimensions": runtime.settings.embedding_dimensions,
            "reranker_model": runtime.settings.reranker_model if use_rerank else None,
            "retrieval": f"{channels}{' +rerank' if use_rerank else ''}",
            "mode": "retrieval-only" if retrieval_only else ("agentic" if agentic else "full"),
        }
        report_path = write_report(reports, config, Path("evals/reports"))
        await persist_report(reports, config, runtime)
        if gate:
            gate_msgs = gate_failures(_overall(reports))
    finally:
        await runtime.aclose()

    _print_eval_summary(reports)
    typer.secho(f"\nReport written to {report_path}", fg=typer.colors.GREEN)
    if gate and gate_msgs:
        for msg in gate_msgs:
            typer.secho(f"GATE FAIL: {msg}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if gate:
        typer.secho("GATE PASS", fg=typer.colors.GREEN)


def _print_eval_summary(reports) -> None:
    from repo_assistant.evaluation.harness import _overall, overall_by_category

    typer.secho("\nEvaluation baseline", fg=typer.colors.CYAN, bold=True)
    for report in reports:
        typer.secho(f"\n  {report.dataset}", bold=True)
        for metric, value in report.summary().items():
            typer.echo(f"    {metric:24} {value}")
    typer.secho("\n  OVERALL", fg=typer.colors.CYAN, bold=True)
    for metric, value in _overall(reports).items():
        typer.echo(f"    {metric:24} {value}")
    typer.secho("\n  BY CATEGORY", fg=typer.colors.CYAN, bold=True)
    for category, entry in overall_by_category(reports).items():
        metrics = "  ".join(f"{k}={v}" for k, v in entry.items())
        typer.echo(f"    {category:14} {metrics}")


def _print_answer(answer: Answer) -> None:
    typer.secho("\nassistant", fg=typer.colors.CYAN, bold=True)
    typer.echo(answer.text)
    if answer.citations:
        typer.secho("\nsources:", fg=typer.colors.BLUE)
        for citation in answer.citations:
            typer.echo(f"  - {citation.label()}@{citation.commit[:12]}")
    typer.echo()


def _fail(message: str) -> int:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    return 1


if __name__ == "__main__":
    app()
