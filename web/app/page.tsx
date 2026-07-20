"use client";

import { useCallback, useEffect, useState } from "react";
import { LoginGate } from "@/components/LoginGate";
import { RepoSidebar } from "@/components/RepoSidebar";
import { JobProgress } from "@/components/JobProgress";
import { Chat } from "@/components/Chat";
import { ChatIcon, SignOutIcon, SparkIcon } from "@/components/icons";
import { api, ApiError, type RepoOut, type UserOut } from "@/lib/api";

function shortRepo(url: string) {
  return url.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "");
}

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
        <div className="panel row" style={{ justifyContent: "space-between", padding: "12px 14px" }}>
          <div className="brand">
            <div className="brand-mark">
              <SparkIcon size={19} />
            </div>
            <div>
              <div className="brand-name">Repo Assistant</div>
              <div className="brand-sub">Cited code Q&amp;A</div>
            </div>
          </div>
          <div className="row" style={{ gap: 6 }}>
            {user.avatar_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={user.avatar_url}
                alt={`${user.login} avatar`}
                width={26}
                height={26}
                style={{ borderRadius: "50%", border: "1px solid var(--border-strong)" }}
              />
            )}
            <button
              className="icon-btn ghost"
              onClick={signOut}
              aria-label={`Sign out ${user.login}`}
              title={`Sign out (${user.login})`}
            >
              <SignOutIcon size={16} />
            </button>
          </div>
        </div>
        <RepoSidebar
          selectedId={repo?.id ?? null}
          onSelect={setRepo}
          onAuthError={onAuthError}
        />
      </div>

      <div>
        {!repo && (
          <div className="panel empty" style={{ minHeight: "70dvh" }}>
            <ChatIcon size={34} className="empty-icon" />
            <div>
              <div style={{ fontWeight: 600, color: "var(--text)" }}>No repository selected</div>
              <p className="small muted" style={{ margin: "4px 0 0" }}>
                Pick one from your library, or index a new repo to start chatting.
              </p>
            </div>
          </div>
        )}
        {repo && repo.status !== "ready" && (
          <div className="panel col">
            <h2 className="chat-title" style={{ fontSize: 15 }}>
              {shortRepo(repo.url)}
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
