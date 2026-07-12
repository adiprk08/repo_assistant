"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyGate } from "@/components/KeyGate";
import { RepoSidebar } from "@/components/RepoSidebar";
import { JobProgress } from "@/components/JobProgress";
import { Chat } from "@/components/Chat";
import { api, type RepoOut } from "@/lib/api";

const KEY_STORAGE = "ra_api_key";

export default function Home() {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [repo, setRepo] = useState<RepoOut | null>(null);

  useEffect(() => {
    setApiKey(localStorage.getItem(KEY_STORAGE));
    setReady(true);
  }, []);

  const saveKey = (key: string) => {
    localStorage.setItem(KEY_STORAGE, key);
    setApiKey(key);
  };

  const clearKey = useCallback(() => {
    localStorage.removeItem(KEY_STORAGE);
    setApiKey(null);
    setRepo(null);
  }, []);

  const refreshRepo = useCallback(async () => {
    if (!apiKey || !repo) return;
    try {
      const fresh = await api.getRepo(apiKey, repo.id);
      setRepo({
        id: fresh.id,
        url: fresh.url,
        default_ref: fresh.default_ref,
        status: fresh.status,
        created_at: fresh.created_at,
      });
    } catch {
      // ignore transient errors; the sidebar poll will recover
    }
  }, [apiKey, repo]);

  if (!ready) return null;
  if (!apiKey) return <KeyGate onSubmit={saveKey} />;

  return (
    <div className="app">
      <div className="col">
        <RepoSidebar
          apiKey={apiKey}
          selectedId={repo?.id ?? null}
          onSelect={setRepo}
          onAuthError={clearKey}
        />
        <button className="small" onClick={clearKey}>
          Sign out
        </button>
      </div>

      <div>
        {!repo && (
          <div className="panel muted">Select or register a repository to start chatting.</div>
        )}
        {repo && repo.status !== "ready" && (
          <div className="panel col">
            <h2 style={{ margin: 0, fontSize: 16 }}>
              {repo.url.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "")}
            </h2>
            <JobProgress apiKey={apiKey} repoId={repo.id} onReady={refreshRepo} />
            <span className="small muted">
              The worker must be running (<code>ra worker</code>) to process the job.
            </span>
          </div>
        )}
        {repo && repo.status === "ready" && (
          <Chat apiKey={apiKey} repo={repo} onAuthError={clearKey} />
        )}
      </div>
    </div>
  );
}
