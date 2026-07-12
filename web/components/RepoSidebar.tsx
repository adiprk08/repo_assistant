"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type RepoOut } from "@/lib/api";

export function RepoSidebar({
  apiKey,
  selectedId,
  onSelect,
  onAuthError,
}: {
  apiKey: string;
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
      setRepos(await api.listRepos(apiKey));
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
  }, [apiKey]);

  async function register(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { repo } = await api.registerRepo(apiKey, url.trim());
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
    <div className="panel col">
      <h2 style={{ margin: 0, fontSize: 16 }}>Repositories</h2>
      <form className="col" onSubmit={register}>
        <input
          placeholder="https://github.com/owner/repo"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button className="primary" type="submit" disabled={busy || !url.trim()}>
          {busy ? "Registering…" : "Index a repo"}
        </button>
      </form>
      {error && <div className="banner small">{error}</div>}
      <div className="col" style={{ gap: 6 }}>
        {repos.length === 0 && <span className="muted small">No repositories yet.</span>}
        {repos.map((r) => (
          <button
            key={r.id}
            className={`repo-item${r.id === selectedId ? " selected" : ""}`}
            onClick={() => onSelect(r)}
          >
            <span className="mono small" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {r.url.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "")}
            </span>
            <span className={`pill${r.status === "ready" ? " ready" : r.status === "failed" ? " failed" : ""}`}>
              {r.status}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
