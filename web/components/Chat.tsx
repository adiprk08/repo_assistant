"use client";

import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  githubLineUrl,
  streamSse,
  type ChatDone,
  type Citation,
  type RepoOut,
} from "@/lib/api";

interface Turn {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  meta?: { path: string; refused: boolean | null };
}

export function Chat({
  apiKey,
  repo,
  onAuthError,
}: {
  apiKey: string;
  repo: RepoOut;
  onAuthError: () => void;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [path, setPath] = useState<"auto" | "fast" | "agent">("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // A fresh session (pinned to the repo's current snapshot) per repo selection.
  useEffect(() => {
    setTurns([]);
    setSessionId(null);
    setError(null);
    api
      .createSession(apiKey, repo.id)
      .then((s) => setSessionId(s.id))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) onAuthError();
        else setError(String((e as Error).message));
      });
  }, [apiKey, repo.id, onAuthError]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const question = input.trim();
    if (!question || !sessionId || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setTurns((t) => [...t, { role: "user", text: question }, { role: "assistant", text: "" }]);
    const assistantIdx = turns.length + 1;

    try {
      await streamSse(
        apiKey,
        `/repos/${repo.id}/chat`,
        { question, path, session_id: sessionId },
        {
          onEvent: (event, data) => {
            if (event === "token") {
              const delta = (data as { text: string }).text;
              setTurns((t) => {
                const copy = [...t];
                copy[assistantIdx] = { ...copy[assistantIdx], text: copy[assistantIdx].text + delta };
                return copy;
              });
            } else if (event === "done") {
              const d = data as ChatDone;
              setTurns((t) => {
                const copy = [...t];
                copy[assistantIdx] = {
                  ...copy[assistantIdx],
                  citations: d.citations,
                  meta: { path: d.path, refused: d.refused },
                };
                return copy;
              });
            } else if (event === "error") {
              setError((data as { detail: string }).detail);
            }
          },
        },
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onAuthError();
      else setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const ready = repo.status === "ready";

  return (
    <div className="panel col" style={{ minHeight: "70vh" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>
          {repo.url.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "")}
        </h2>
        <select value={path} onChange={(e) => setPath(e.target.value as typeof path)} style={{ width: 120 }}>
          <option value="auto">auto</option>
          <option value="fast">fast</option>
          <option value="agent">agent</option>
        </select>
      </div>

      <div className="messages" style={{ flex: 1 }}>
        {turns.length === 0 && (
          <p className="muted small">
            {ready ? "Ask a question about this repository." : "This repo is not indexed yet."}
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={`msg ${turn.role}`}>
            {turn.text || (turn.role === "assistant" && busy ? "…" : "")}
            {turn.meta && (
              <div className="small muted" style={{ marginTop: 6 }}>
                path: {turn.meta.path}
                {turn.meta.refused ? " · no grounded answer found" : ""}
              </div>
            )}
            {turn.citations?.map((c, j) => (
              <div key={j} className="citation">
                <a href={githubLineUrl(repo.url, c.commit, c)} target="_blank" rel="noreferrer">
                  {c.path}:{c.start_line === c.end_line ? c.start_line : `${c.start_line}-${c.end_line}`}
                </a>
                <pre>{c.cited_text}</pre>
              </div>
            ))}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {error && <div className="banner small">{error}</div>}

      <form className="row" onSubmit={send}>
        <input
          placeholder={ready ? "Ask about the code…" : "Waiting for indexing…"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={!ready || !sessionId || busy}
        />
        <button className="primary" type="submit" disabled={!ready || !sessionId || busy || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
