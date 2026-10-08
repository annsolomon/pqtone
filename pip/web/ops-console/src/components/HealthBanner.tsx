import { useEffect, useState } from "react";
import { get } from "../api";
import { dateTime } from "../format";
import type { PipelineHealth } from "../types";

export function HealthBanner() {
  const [health, setHealth] = useState<PipelineHealth | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => get<PipelineHealth>("/api/pipeline/health").then((h) => alive && setHealth(h)).catch(() => undefined);
    void load();
    const t = window.setInterval(load, 15_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);
  if (!health || health.status === "ok") return null;
  return (
    <div className="banner" role="status">
      The rules engine has not reported in {health.lastHeartbeatAt ? `since ${dateTime(health.lastHeartbeatAt)}` : "yet"}.
      New incidents may be delayed until it recovers.
    </div>
  );
}
