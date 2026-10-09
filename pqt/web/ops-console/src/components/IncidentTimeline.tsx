import { useEffect, useState } from "react";
import { get } from "../api";
import { clock } from "../format";
import { buildChart, describe, DEFAULT_SIZE } from "../timeline";
import type { IncidentTimelineData } from "../types";

/**
 * Milestone C4: why did this fire? Queue length (or zone entries) around onset, with the threshold,
 * the breach/alert/clear moments and register changes. The same data is available as a table.
 */
export function IncidentTimeline({ incidentId, version }: { incidentId: string; version: number }) {
  const [data, setData] = useState<IncidentTimelineData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    get<IncidentTimelineData>(`/api/incidents/${incidentId}/timeline`)
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [incidentId, version]);

  if (error) return <section className="timeline"><h2>Timeline</h2><p className="error">Couldn't load the timeline: {error}</p></section>;
  if (!data) return <section className="timeline"><h2>Timeline</h2><p className="muted">Loading timeline…</p></section>;

  const m = buildChart(data);
  const size = DEFAULT_SIZE;
  const label = describe(data);
  const valueName = data.kind === "zone" ? "Entries" : "Queue length";

  return (
    <section className="timeline" aria-labelledby="timeline-h">
      <h2 id="timeline-h">Timeline</h2>
      {m.empty ? (
        <p className="empty">{label}</p>
      ) : (
        <>
          <svg
            className="timeline-chart"
            viewBox={`0 0 ${size.width} ${size.height}`}
            role="img"
            aria-label={label}
            preserveAspectRatio="xMidYMid meet"
          >
            {m.yTicks.map((t) => (
              <g key={`y${t.label}`} className="tl-grid">
                <line x1={size.padLeft} x2={size.width - size.padRight} y1={t.y} y2={t.y} />
                <text x={size.padLeft - 6} y={t.y} dy="0.32em" textAnchor="end">{t.label}</text>
              </g>
            ))}
            {m.xTicks.map((t) => (
              <text key={`x${t.label}`} className="tl-xtick" x={t.x} y={size.height - 8}
                textAnchor={t.x > size.width - size.padRight - 18 ? "end" : "middle"}>{t.label}</text>
            ))}
            {m.thresholdY != null && (
              <g className="tl-threshold">
                <line x1={size.padLeft} x2={size.width - size.padRight} y1={m.thresholdY} y2={m.thresholdY} />
                <text x={size.width - size.padRight} y={m.thresholdY - 4} textAnchor="end">threshold {data.threshold}</text>
              </g>
            )}
            {m.moments.map((mo) => (
              <g key={mo.kind} className={`tl-moment tl-${mo.kind}`}>
                <line x1={mo.x} x2={mo.x} y1={size.padTop} y2={size.height - size.padBottom} />
                <title>{mo.label}</title>
              </g>
            ))}
            {m.bars.map((b) => (
              <rect key={b.t} className="tl-bar" x={b.x} y={b.y} width={b.w} height={b.h}><title>{`${clock(b.t)}: ${b.value}`}</title></rect>
            ))}
            {m.linePath && <path className="tl-line" d={m.linePath} />}
            {m.markers.map((mk, i) => (
              <g key={i} className={mk.opened ? "tl-reg tl-reg-open" : "tl-reg tl-reg-close"}>
                <path d={`M${mk.x - 5},${size.height - size.padBottom} l5,-8 l5,8 z`} />
                <title>{mk.label}</title>
              </g>
            ))}
          </svg>
          <ul className="tl-legend small" aria-hidden="true">
            <li><span className={data.kind === "zone" ? "sw sw-bar" : "sw sw-line"} /> {valueName}</li>
            {m.thresholdY != null && <li><span className="sw sw-threshold" /> Threshold</li>}
            <li><span className="sw sw-onset" /> Breach started</li>
            <li><span className="sw sw-detected" /> Alert raised</li>
            {data.resolvedAt && <li><span className="sw sw-cleared" /> Cleared</li>}
            {m.markers.length > 0 && <li><span className="sw sw-reg" /> Register change</li>}
          </ul>
          {data.truncated && <p className="muted small">Showing the first 5,000 points only.</p>}
          <details className="tl-table">
            <summary>Show the data as a table</summary>
            <table>
              <caption className="small muted">Times in UTC (event time)</caption>
              <thead>
                <tr><th scope="col">Time</th><th scope="col">{valueName}</th>{data.kind === "queue" && <th scope="col">Open registers</th>}</tr>
              </thead>
              <tbody>
                {data.series.map((p) => (
                  <tr key={p.t}>
                    <td className="num">{clock(p.t)}</td>
                    <td className="num">{data.kind === "zone" ? p.entries : p.length}</td>
                    {data.kind === "queue" && <td className="num">{p.openRegisters ?? "–"}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
            {data.markers.length > 0 && (
              <ul className="small">
                {data.markers.map((mk, i) => (
                  <li key={i}><span className="num">{clock(mk.t)}</span> {mk.registerId} {mk.kind === "register.opened" ? "opened" : "closed"}</li>
                ))}
              </ul>
            )}
          </details>
        </>
      )}
    </section>
  );
}
