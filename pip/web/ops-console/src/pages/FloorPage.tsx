import { useCallback, useEffect, useRef, useState } from "react";
import { useAlerts } from "../alerts/AlertProvider";
import { get } from "../api";
import { EventTicker } from "../components/EventTicker";
import { FloorMap } from "../components/FloorMap";
import { IncidentRail } from "../components/IncidentRail";
import { applyEvents } from "../floor";
import { useLive } from "../live";
import type { Incident, Layout, LiveEvent, StoreState } from "../types";

const OPEN = new Set(["OPEN", "ACKNOWLEDGED"]);
const TICKER_SIZE = 40;

export function FloorPage() {
  const live = useLive();
  const { pulsing } = useAlerts();
  const [stores, setStores] = useState<{ storeId: string; name: string }[]>([]);
  const [storeId, setStoreId] = useState<string | null>(null);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [state, setState] = useState<StoreState | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [ticker, setTicker] = useState<LiveEvent[]>([]);
  const [skipped, setSkipped] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const refreshTimer = useRef<number | null>(null);

  useEffect(() => {
    get<{ storeId: string; name: string }[]>("/api/stores")
      .then((s) => {
        setStores(s);
        setStoreId((cur) => cur ?? s[0]?.storeId ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const reloadState = useCallback(async (id: string) => {
    setState(await get<StoreState>(`/api/stores/${id}/state`));
  }, []);

  useEffect(() => {
    if (!storeId) return;
    setError(null);
    Promise.all([
      get<Layout>(`/api/stores/${storeId}/layout`).then(setLayout),
      reloadState(storeId),
      get<Incident[]>(`/api/incidents?mode=enforce&status=OPEN,ACKNOWLEDGED&storeId=${storeId}&limit=50`).then(setIncidents),
      get<LiveEvent[]>(`/api/events/recent?storeId=${storeId}&limit=${TICKER_SIZE}`).then(setTicker),
    ]).catch((e: Error) => setError(e.message));
  }, [storeId, reloadState]);

  useEffect(() => {
    if (!storeId) return;
    const offEvents = live.onEvents((batch) => {
      const mine = batch.items.filter((e) => e.storeId === storeId);
      if (mine.length) {
        setState((s) => (s ? applyEvents(s, mine) : s));
        setTicker((t) => [...mine.filter((e) => e.type !== "clock.tick").reverse(), ...t].slice(0, TICKER_SIZE));
      }
      setSkipped(batch.skipped);
      // Coalesced batches drop events, so periodically reconcile with the server's read model.
      if (batch.skipped > 0 && refreshTimer.current === null) {
        refreshTimer.current = window.setTimeout(() => {
          refreshTimer.current = null;
          void reloadState(storeId);
        }, 2_000);
      }
    });
    const offIncidents = live.onIncident(({ incident }) => {
      if (incident.storeId !== storeId || incident.mode !== "enforce") return;
      setIncidents((list) => {
        const rest = list.filter((i) => i.incidentId !== incident.incidentId);
        return OPEN.has(incident.status) ? [incident, ...rest] : rest;
      });
    });
    return () => {
      offEvents();
      offIncidents();
      if (refreshTimer.current !== null) window.clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    };
  }, [live, storeId, reloadState]);

  const threshold = Number(incidents.find((i) => i.ruleId === "R-QUEUE-001")?.attrs["threshold"] ?? 6);

  return (
    <main className="floor-page">
      <div className="page-head">
        <h1>{layout?.name ?? "Floor"}</h1>
        {stores.length > 1 && (
          <label className="store-pick">
            Store
            <select value={storeId ?? ""} onChange={(e) => setStoreId(e.target.value)}>
              {stores.map((s) => <option key={s.storeId} value={s.storeId}>{s.name}</option>)}
            </select>
          </label>
        )}
        {state?.simRunId && <span className="muted" data-testid="sim-run">Simulation {state.simRunId}</span>}
      </div>
      {error && <p className="error">Could not load the floor: {error}</p>}
      <div className="floor-grid">
        <div className="floor-main">
          {layout && state ? <FloorMap layout={layout} state={state} incidents={incidents} threshold={threshold} pulsing={pulsing} />
            : <div className="floor placeholder" aria-busy="true" />}
          <EventTicker events={ticker} skipped={skipped} />
        </div>
        <IncidentRail incidents={incidents} />
      </div>
    </main>
  );
}
