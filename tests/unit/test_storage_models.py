"""Structural checks on the ORM models — no database connection required.
Guards against typos in table/column names that would only surface at migration time.
"""

from repo_assistant.storage.models import Base, File, Job, Repo, Snapshot


def test_all_models_are_registered_on_the_shared_metadata() -> None:
    table_names = set(Base.metadata.tables)
    assert table_names == {
        "repos",
        "snapshots",
        "files",
        "jobs",
        "symbols",
        "chunks",
        "embedding_cache",
        "eval_runs",
        "eval_results",
        "edges",
        "chat_sessions",
        "chat_messages",
        "api_keys",
    }


def test_repo_snapshot_foreign_key_is_bidirectional() -> None:
    assert "repo_id" in Snapshot.__table__.columns
    assert "active_snapshot_id" in Repo.__table__.columns
    (fk,) = Snapshot.__table__.columns["repo_id"].foreign_keys
    assert fk.column.table.name == "repos"


def test_file_belongs_to_a_snapshot() -> None:
    (fk,) = File.__table__.columns["snapshot_id"].foreign_keys
    assert fk.column.table.name == "snapshots"


def test_job_belongs_to_a_repo() -> None:
    (fk,) = Job.__table__.columns["repo_id"].foreign_keys
    assert fk.column.table.name == "repos"
