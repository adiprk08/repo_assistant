"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type RepoOut } from "@/lib/api";
import { AlertIcon, PlusIcon } from "@/components/icons";

function shortRepo(url: string) {
  return url.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "");
}

export function RepoSidebar({
  selectedId,
  onSelect,
  onAuthError,
}: {
  selectedId: string | null;
  onSelect: (repo: RepoOut) => void;
  onAuthError: () => void;
}) {
  const [repos, setRepos] = useState<RepoOut[]>([]);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setRepos(await api.listRepos());
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onAuthError();
      else setError(String((e as Error).message));
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 4000); // reflect indexing status changes
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function register(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { repo } = await api.registerRepo(url.trim());
      setUrl("");
      await refresh();
      onSelect(repo);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onAuthError();
      else setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel col" style={{ flex: 1 }}>
      <form className="col" onSubmit={register} style={{ gap: 8 }}>
        <label className="section-label" htmlFor="repo-url">
          Index a repository
        </label>
        <input
          id="repo-url"
          placeholder="https://github.com/owner/repo"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button className="primary" type="submit" disabled={busy || !url.trim()}>
          {busy ? (
            "Registering…"
          ) : (
            <>
              <PlusIcon size={16} />
              Add repository
            </>
          )}
        </button>
      </form>

      {error && (
        <div className="banner small">
          <AlertIcon size={15} />
          <span>{error}</span>
        </div>
      )}

      <div className="col" style={{ gap: 8 }}>
        <span className="section-label">Your library</span>
        {repos.length === 0 && (
          <span className="muted small">No repositories yet — add one above.</span>
        )}
        {repos.map((r) => (
          <button
            key={r.id}
            className={`repo-item${r.id === selectedId ? " selected" : ""}`}
            onClick={() => onSelect(r)}
            aria-pressed={r.id === selectedId}
          >
            <span className="mono small repo-name">{shortRepo(r.url)}</span>
            <span
              className={`pill ${r.status}`}
            >
              {r.status}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
