"use client";

import { useEffect, useState } from "react";
import { streamSse, type JobOut } from "@/lib/api";

// Ordered ingestion stages, so we can render a rough completion bar.
const STAGES = ["cloning", "scanning", "parsing", "enriching", "embedding", "indexing", "ready"];

export function JobProgress({
  repoId,
  onReady,
}: {
  repoId: string;
  onReady: () => void;
}) {
  const [job, setJob] = useState<JobOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let settled = false;
    streamSse(
      `/repos/${repoId}/job/stream`,
      null,
      {
        signal: controller.signal,
        onEvent: (event, data) => {
          if (event === "progress") setJob(data as JobOut);
          else if (event === "done") {
            settled = true;
            const state = (data as { state: string }).state;
            if (state === "succeeded") onReady();
            else setError((data as { error?: string }).error || "Ingestion failed.");
          } else if (event === "error") {
            setError((data as { detail: string }).detail);
          }
        },
      },
      "GET",
    ).catch((e) => {
      if (!settled && !controller.signal.aborted) setError(String(e.message || e));
    });
    return () => controller.abort();
  }, [repoId, onReady]);

  const stageIndex = job ? STAGES.indexOf(job.stage) : 0;
  const pct = Math.max(5, Math.round(((stageIndex + 1) / STAGES.length) * 100));

  if (error) return <div className="banner">{error}</div>;
  return (
    <div className="col">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="small muted">Indexing…</span>
        <span className="small mono">{job?.stage || "queued"}</span>
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      {job?.progress && Object.keys(job.progress).length > 0 && (
        <div className="small muted mono">
          {Object.entries(job.progress)
            .map(([k, v]) => `${k}: ${String(v)}`)
            .join("  ·  ")}
        </div>
      )}
    </div>
  );
}
