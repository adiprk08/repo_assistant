"""Repo Assistant CLI: index a repository and chat with it (Phase 1)."""

import asyncio

import typer

from repo_assistant import __version__
from repo_assistant.core.errors import NotFoundError, ProviderError
from repo_assistant.reasoning import generate_answer
from repo_assistant.reasoning.service import Answer
from repo_assistant.retrieval import hybrid_retrieve

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
) -> None:
    """Clone, parse, and index a GitHub repository."""
    asyncio.run(_index(github_url, ref))


@app.command()
def chat(
    repo: str = typer.Argument(..., help="Repository URL or id of an already-indexed repo."),
) -> None:
    """Start an interactive, cited chat session over an indexed repository."""
    asyncio.run(_chat(repo))


@app.command()
def eval(
    datasets_dir: str = typer.Option("evals/datasets", "--datasets", help="Golden dataset dir."),
    dense_only: bool = typer.Option(
        False, "--dense-only", help="Ablation: dense channel only (disable the symbol channel)."
    ),
) -> None:
    """Run the golden evaluation datasets and record a baseline report."""
    from pathlib import Path

    dataset_paths = sorted(Path(datasets_dir).glob("*.yaml"))
    if not dataset_paths:
        raise typer.Exit(code=_fail(f"No datasets found in {datasets_dir}"))
    asyncio.run(_eval(dataset_paths, use_symbols=not dense_only))


async def _index(github_url: str, ref: str | None) -> None:
    from repo_assistant.cli.runtime import build_runtime
    from repo_assistant.indexing.pipeline import index_repository

    runtime = build_runtime()
    try:
        typer.echo(f"Indexing {github_url} ...")
        result = await index_repository(
            github_url,
            embedder=runtime.embedder(),
            vector_index=runtime.vector_index,
            session_factory=runtime.session_factory,
            ref=ref,
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


async def _chat(identifier: str) -> None:
    from repo_assistant.cli.runtime import build_runtime, resolve_indexed_repo

    runtime = build_runtime()
    try:
        try:
            resolved = await resolve_indexed_repo(runtime, identifier)
        except NotFoundError as exc:
            raise typer.Exit(code=_fail(str(exc))) from exc

        embedder = runtime.embedder()
        llm = runtime.llm()
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
                retrieved = await hybrid_retrieve(
                    str(resolved.repo_id),
                    str(resolved.snapshot_id),
                    question,
                    embedder=embedder,
                    vector_index=runtime.vector_index,
                    session_factory=runtime.session_factory,
                    commit=resolved.commit_sha,
                )
                answer = await generate_answer(question, retrieved, llm=llm)
            except ProviderError as exc:
                typer.secho(f"  provider error: {exc}", fg=typer.colors.RED)
                continue
            _print_answer(answer)
    finally:
        await runtime.aclose()


async def _eval(dataset_paths: list, *, use_symbols: bool) -> None:
    from pathlib import Path

    from repo_assistant.cli.runtime import build_runtime
    from repo_assistant.evaluation import DatasetSpec, run_dataset
    from repo_assistant.evaluation.harness import write_report

    runtime = build_runtime()
    reports = []
    try:
        for path in dataset_paths:
            dataset = DatasetSpec.from_yaml(path)
            typer.echo(f"Evaluating {path.stem} ({len(dataset.questions)} questions) ...")
            try:
                reports.append(await run_dataset(dataset, runtime, use_symbols=use_symbols))
            except (NotFoundError, ProviderError) as exc:
                raise typer.Exit(code=_fail(str(exc))) from exc

        config = {
            "generation_model": runtime.settings.generation_model,
            "embedding_model": runtime.settings.embedding_model,
            "embedding_dimensions": runtime.settings.embedding_dimensions,
            "retrieval": "hybrid(dense+symbol)" if use_symbols else "dense-only",
        }
        report_path = write_report(reports, config, Path("evals/reports"))
    finally:
        await runtime.aclose()

    _print_eval_summary(reports)
    typer.secho(f"\nReport written to {report_path}", fg=typer.colors.GREEN)


def _print_eval_summary(reports) -> None:
    from repo_assistant.evaluation.harness import _overall

    typer.secho("\nEvaluation baseline", fg=typer.colors.CYAN, bold=True)
    for report in reports:
        typer.secho(f"\n  {report.dataset}", bold=True)
        for metric, value in report.summary().items():
            typer.echo(f"    {metric:24} {value}")
    typer.secho("\n  OVERALL", fg=typer.colors.CYAN, bold=True)
    for metric, value in _overall(reports).items():
        typer.echo(f"    {metric:24} {value}")


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
