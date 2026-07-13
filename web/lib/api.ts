// Typed client for the Repo Assistant API.
//
// Auth is cookie-based (docs/adr/0023): every call goes to the same-origin
// /api/* proxy with credentials, so the browser's session cookie authenticates
// it. SSE is consumed via fetch + a ReadableStream reader (not EventSource), the
// same parser driving job-progress and chat streams.

const API_BASE = "/api";

export interface UserOut {
  id: string;
  login: string;
  name: string | null;
  avatar_url: string | null;
}

export interface RepoOut {
  id: string;
  url: string;
  default_ref: string;
  status: string;
  created_at: string;
}

export interface JobOut {
  id: string;
  repo_id: string;
  stage: string;
  state: string;
  progress: Record<string, unknown>;
  error: string | null;
}

export interface SessionOut {
  id: string;
  repo_id: string;
  commit_sha: string;
  title: string | null;
}

export interface Citation {
  path: string;
  start_line: number;
  end_line: number;
  commit: string;
  cited_text: string;
}

export interface ChatDone {
  path: string;
  intent: string;
  n_tool_calls: number;
  forced_stop: boolean;
  refused: boolean | null;
  citations: Citation[];
}

export interface SearchHit {
  chunk_id: string;
  path: string;
  start_line: number;
  end_line: number;
  score: number;
  symbol: string | null;
  language: string | null;
  excerpt: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  me: () => request<UserOut>("/auth/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  listRepos: () => request<RepoOut[]>("/repos"),
  getRepo: (id: string) =>
    request<RepoOut & { active_snapshot: unknown; latest_job: JobOut | null }>(`/repos/${id}`),
  registerRepo: (url: string, ref?: string, enrich = false) =>
    request<{ repo: RepoOut; job: JobOut }>("/repos", {
      method: "POST",
      body: JSON.stringify({ url, ref: ref || null, enrich }),
    }),
  createSession: (repoId: string, title?: string) =>
    request<SessionOut>(`/repos/${repoId}/sessions`, {
      method: "POST",
      body: JSON.stringify({ title: title || null }),
    }),
  search: (repoId: string, query: string) =>
    request<{ commit: string; results: SearchHit[] }>(`/repos/${repoId}/search`, {
      method: "POST",
      body: JSON.stringify({ query, limit: 12 }),
    }),
};

// The URL that starts the GitHub OAuth login (a full-page navigation).
export const LOGIN_URL = `${API_BASE}/auth/github/login`;

export interface SseHandlers {
  onEvent: (event: string, data: unknown) => void;
  signal?: AbortSignal;
}

// POST a request and parse the SSE response body (event:/data: frames).
export async function streamSse(
  path: string,
  body: unknown,
  handlers: SseHandlers,
  method = "POST",
): Promise<void> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: method === "GET" ? undefined : JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!resp.ok || !resp.body) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail || detail;
    } catch {
      // keep statusText
    }
    throw new ApiError(resp.status, detail);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) {
        let data: unknown = dataLines.join("\n");
        try {
          data = JSON.parse(data as string);
        } catch {
          // leave as string
        }
        handlers.onEvent(event, data);
      }
    }
  }
}

export function githubLineUrl(repoUrl: string, commit: string, c: Citation): string {
  // Turn a repo URL + pinned commit + span into a deep link to the exact lines.
  const slug = repoUrl.replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, "");
  const lines = c.start_line === c.end_line ? `L${c.start_line}` : `L${c.start_line}-L${c.end_line}`;
  return `https://github.com/${slug}/blob/${commit}/${c.path}#${lines}`;
}

export { API_BASE };
