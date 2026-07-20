"use client";

import { LOGIN_URL } from "@/lib/api";
import { GitHubIcon, SparkIcon } from "@/components/icons";

const BENEFITS = [
  "Index any public GitHub repo and ask about it in natural language",
  "Answers grounded in the code, with commit-pinned citations you can open",
  "Your library and conversations stay private to your account",
];

function Check() {
  return (
    <svg
      className="check"
      width={16}
      height={16}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.25}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export function LoginGate() {
  return (
    <div className="login-wrap">
      <div className="panel login-card">
        <div className="brand-mark" style={{ width: 46, height: 46, borderRadius: 12 }}>
          <SparkIcon size={26} />
        </div>
        <div>
          <h1>Repo Assistant</h1>
          <p className="muted small" style={{ margin: "6px 0 0", maxWidth: 320 }}>
            Chat with any GitHub repository — grounded in the actual code, with verifiable
            citations.
          </p>
        </div>

        <ul className="login-benefits">
          {BENEFITS.map((b) => (
            <li key={b}>
              <Check />
              <span>{b}</span>
            </li>
          ))}
        </ul>

        <a className="primary btn-github" href={LOGIN_URL}>
          <GitHubIcon size={19} />
          Sign in with GitHub
        </a>
        <span className="login-fineprint">We only request your public profile.</span>
      </div>
    </div>
  );
}
