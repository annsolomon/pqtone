import { occupancyStrength, queueTrail } from "../floor";
import type { Incident, Layout, StoreState } from "../types";

interface Props {
  layout: Layout;
  state: StoreState;
  incidents: Incident[];
  threshold: number;
  /** Incident ids whose zone outline is animating because they just opened. */
  pulsing?: Set<string>;
}

function zoneForIncident(inc: Incident, layout: Layout): string | null {
  const s = inc.subject ?? "";
  if (s.startsWith("zone:")) return s.slice(5);
  if (s.startsWith("queue:")) return layout.queues.find((q) => q.id === s.slice(6))?.zoneId ?? null;
  return null;
}

export function FloorMap({ layout, state, incidents, threshold, pulsing }: Props) {
  const alerting = new Map<string, Incident["severity"]>();
  const pulsingZones = new Set<string>();
  for (const inc of incidents) {
    const z = zoneForIncident(inc, layout);
    if (!z) continue;
    if (pulsing?.has(inc.incidentId)) pulsingZones.add(z);
    const prev = alerting.get(z);
    if (!prev || inc.severity === "high" || (inc.severity === "medium" && prev === "low")) alerting.set(z, inc.severity);
  }
  const queue = layout.queues[0];
  const q = queue ? state.queues[queue.id] : undefined;
  const checkout = queue ? layout.zones.find((z) => z.id === queue.zoneId) : undefined;

  return (
    <svg className="floor" viewBox={`0 0 ${layout.width} ${layout.height}`} role="img"
         aria-label={`Floor plan of ${layout.name} with live occupancy`}>
      <rect x="0" y="0" width={layout.width} height={layout.height} className="floor-bg" />
      {layout.zones.map((z) => {
        const count = state.zones[z.id] ?? 0;
        const sev = alerting.get(z.id);
        return (
          <g key={z.id} className={`zone kind-${z.kind}${sev ? ` alert alert-${sev}` : ""}${pulsingZones.has(z.id) ? " alert-new" : ""}`}
             data-zone={z.id} data-alert={sev ?? undefined}>
            <rect x={z.x} y={z.y} width={z.w} height={z.h} rx="6" className="zone-fill"
                  style={{ fillOpacity: occupancyStrength(count) }} />
            <rect x={z.x} y={z.y} width={z.w} height={z.h} rx="6" className="zone-edge" />
            <text x={z.x + 14} y={z.y + 26} className="zone-name">{z.name}</text>
            {z.kind !== "checkout" && (
              <text x={z.x + z.w - 14} y={z.y + z.h - 16} className="zone-count" textAnchor="end">{count}</text>
            )}
            {z.kind === "monitored" && (
              <text x={z.x + 14} y={z.y + 46} className="zone-note">Flagged after 10 min</text>
            )}
          </g>
        );
      })}
      {checkout && queue && (
        <g className="queue">
          {queueTrail(q?.length ?? 0, { x: checkout.x + 16, y: checkout.y + 92, w: checkout.w - 92, h: checkout.h - 108 }).map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r="7" className={i + 1 >= threshold ? "dot over" : "dot"} />
          ))}
          <text x={checkout.x + 14} y={checkout.y + 54} className="queue-count" data-testid="queue-count">
            {q?.length ?? 0}
            <tspan className="queue-unit"> waiting</tspan>
          </text>
          <text x={checkout.x + 14} y={checkout.y + 76} className="zone-note">Alert when {threshold} or more wait for a minute</text>
          {layout.registers.map((r, i) => (
            <g key={r.id}>
              <rect x={checkout.x + checkout.w - 56} y={checkout.y + 96 + i * 52} width="40" height="36" rx="4"
                    className={i < (q?.openRegisters ?? 0) ? "register open" : "register"} />
              <text x={checkout.x + checkout.w - 36} y={checkout.y + 119 + i * 52} textAnchor="middle" className="register-label">
                {i + 1}
              </text>
            </g>
          ))}
        </g>
      )}
    </svg>
  );
}
