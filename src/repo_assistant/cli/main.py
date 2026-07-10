import typer

from repo_assistant import __version__

app = typer.Typer(name="ra", help="Repo Assistant - a RAG-powered GitHub repository assistant.")


@app.command()
def version() -> None:
    """Print the installed repo-assistant version."""
    typer.echo(__version__)


@app.command()
def index(github_url: str) -> None:
    """Clone, parse, and index a GitHub repository. (Implemented in Phase 1.)"""
    raise NotImplementedError("`ra index` lands in Phase 1 — see docs/ROADMAP.md")


@app.command()
def chat(repo: str) -> None:
    """Start an interactive, cited chat session over an indexed repository. (Implemented in Phase 1.)"""
    raise NotImplementedError("`ra chat` lands in Phase 1 — see docs/ROADMAP.md")


if __name__ == "__main__":
    app()
