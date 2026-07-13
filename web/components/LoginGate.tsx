"use client";

import { LOGIN_URL } from "@/lib/api";

export function LoginGate() {
  return (
    <div className="app" style={{ gridTemplateColumns: "1fr", placeItems: "center" }}>
      <div className="panel col" style={{ maxWidth: 460, width: "100%", textAlign: "center" }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Repo Assistant</h1>
        <p className="muted small" style={{ margin: 0 }}>
          Sign in with GitHub to index repositories and chat with them. Your library and
          conversations are private to your account.
        </p>
        <a className="primary" href={LOGIN_URL} style={{ textDecoration: "none", textAlign: "center" }}>
          Sign in with GitHub
        </a>
      </div>
    </div>
  );
}
