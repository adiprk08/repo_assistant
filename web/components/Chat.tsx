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
import {
  AlertIcon,
  ChatIcon,
  ExternalLinkIcon,
  FileCodeIcon,
  SendIcon,
  SparkIcon,
} from "@/components/icons";

const PATHS = ["auto", "fast", "agent"] as const;

function shortRepo(url: string) {
  return url.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "");
}

function lineLabel(c: Citation) {
  return c.start_line === c.end_line ? `${c.start_line}` : `${c.start_line}-${c.end_line}`;
}

interface Turn {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
  meta?: { path: string; refused: boolean | null };
}

export function Chat({
  repo,
  onAuthError,
}: {
  repo: RepoOut;
  onAuthError: () => void;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [path, setPath] = useState<"auto" | "fast" | "agent">("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // A fresh session (pinned to the repo's current snapshot) per repo selection.
  useEffect(() => {
    setTurns([]);
    setSessionId(null);
    setError(null);
    api
      .createSession(repo.id)
      .then((s) => setSessionId(s.id))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) onAuthError();
        else setError(String((e as Error).message));
      });
  }, [repo.id, onAuthError]);

  useEffect(() => {
    // Keep the newest turn in view by scrolling the messages pane itself, not
    // the page.
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
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
    <div className="panel chat-panel">
      <div className="chat-header">
        <h2 className="chat-title" title={shortRepo(repo.url)}>
          {shortRepo(repo.url)}
        </h2>
        <div className="segmented" role="group" aria-label="Reasoning path">
          {PATHS.map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={path === p}
              onClick={() => setPath(p)}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="messages" ref={listRef}>
        {turns.length === 0 && (
          <div className="empty">
            <ChatIcon size={30} className="empty-icon" />
            <div>
              <div style={{ fontWeight: 600, color: "var(--text)" }}>
                {ready ? "Ask about this repository" : "This repo is not indexed yet"}
              </div>
              <p className="small muted" style={{ margin: "4px 0 0", maxWidth: 340 }}>
                {ready
                  ? "Answers are grounded in the code and cite the exact lines they came from."
                  : "Indexing must finish before you can chat."}
              </p>
            </div>
          </div>
        )}
        {turns.map((turn, i) => {
          const streaming = turn.role === "assistant" && busy && !turn.text && !turn.meta;
          return (
            <div key={i} className={`turn ${turn.role}`}>
              <div className={`avatar ${turn.role}`} aria-hidden>
                {turn.role === "assistant" ? <SparkIcon size={16} /> : "You"}
              </div>
              <div className="bubble">
                {streaming ? (
                  <span className="typing" aria-label="Assistant is typing">
                    <span />
                    <span />
                    <span />
                  </span>
                ) : (
                  turn.text
                )}
                {turn.meta && (
                  <div className="turn-meta">
                    <span className="turn-tag">{turn.meta.path}</span>
                    {turn.meta.refused && <span>no grounded answer found</span>}
                  </div>
                )}
                {turn.citations?.map((c, j) => (
                  <div key={j} className="citation">
                    <div className="citation-head">
                      <FileCodeIcon size={14} />
                      <a href={githubLineUrl(repo.url, c.commit, c)} target="_blank" rel="noreferrer">
                        {c.path}:{lineLabel(c)}
                      </a>
                      <ExternalLinkIcon size={13} className="ext" />
                    </div>
                    <pre>{c.cited_text}</pre>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>

      {error && (
        <div className="banner small" style={{ margin: "0 16px" }}>
          <AlertIcon size={15} />
          <span>{error}</span>
        </div>
      )}

      <form className="composer" onSubmit={send}>
        <input
          placeholder={ready ? "Ask about the code…" : "Waiting for indexing…"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={!ready || !sessionId || busy}
          aria-label="Your question"
        />
        <button
          className="primary icon-btn"
          type="submit"
          disabled={!ready || !sessionId || busy || !input.trim()}
          aria-label="Send question"
        >
          <SendIcon size={18} />
        </button>
      </form>
    </div>
  );
}
