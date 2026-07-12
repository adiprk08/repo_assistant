"use client";

import { useState } from "react";

export function KeyGate({ onSubmit }: { onSubmit: (key: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="app" style={{ gridTemplateColumns: "1fr", placeItems: "center" }}>
      <div className="panel col" style={{ maxWidth: 460, width: "100%" }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Repo Assistant</h1>
        <p className="muted small" style={{ margin: 0 }}>
          Enter an API key to continue. Create one with{" "}
          <code>ra apikey create &lt;name&gt;</code>. It is stored only in this browser.
        </p>
        <form
          className="col"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) onSubmit(value.trim());
          }}
        >
          <input
            type="password"
            placeholder="ra_..."
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
          />
          <button className="primary" type="submit" disabled={!value.trim()}>
            Continue
          </button>
        </form>
      </div>
    </div>
  );
}
