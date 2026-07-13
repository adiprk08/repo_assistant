"use client";

import { useCallback, useEffect, useState } from "react";
import { LoginGate } from "@/components/LoginGate";
import { RepoSidebar } from "@/components/RepoSidebar";
import { JobProgress } from "@/components/JobProgress";
import { Chat } from "@/components/Chat";
import { api, ApiError, type RepoOut, type UserOut } from "@/lib/api";

export default function Home() {
  const [user, setUser] = useState<UserOut | null>(null);
  const [ready, setReady] = useState(false);
  const [repo, setRepo] = useState<RepoOut | null>(null);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, []);

  // A 401 anywhere means the session ended — drop back to the login gate.
  const onAuthError = useCallback(() => {
    setUser(null);
    setRepo(null);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // best-effort; clear locally regardless
    }
    onAuthError();
  }, [onAuthError]);

  const refreshRepo = useCallback(async () => {
    if (!repo) return;
    try {
      const fresh = await api.getRepo(repo.id);
      setRepo({
        id: fresh.id,
        url: fresh.url,
        default_ref: fresh.default_ref,
        status: fresh.status,
        created_at: fresh.created_at,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onAuthError();
      // otherwise ignore transient errors; the sidebar poll will recover
    }
  }, [repo, onAuthError]);

  if (!ready) return null;
  if (!user) return <LoginGate />;

  return (
    <div className="app">
      <div className="col">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          {user.avatar_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={user.avatar_url}
              alt=""
              width={24}
              height={24}
              style={{ borderRadius: "50%" }}
            />
          )}
          <span className="small mono" style={{ flex: 1, marginLeft: 8 }}>
            {user.login}
          </span>
          <button className="small" onClick={signOut}>
            Sign out
          </button>
        </div>
        <RepoSidebar
          selectedId={repo?.id ?? null}
          onSelect={setRepo}
          onAuthError={onAuthError}
        />
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
            <JobProgress repoId={repo.id} onReady={refreshRepo} />
            <span className="small muted">
              The worker must be running (<code>ra worker</code>) to process the job.
            </span>
          </div>
        )}
        {repo && repo.status === "ready" && (
          <Chat repo={repo} onAuthError={onAuthError} />
        )}
      </div>
    </div>
  );
}
