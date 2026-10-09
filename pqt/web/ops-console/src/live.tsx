import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { Incident, LiveEvent } from "./types";

export interface EventBatch {
  items: LiveEvent[];
  skipped: number;
}
export interface IncidentChange {
  change: "opened" | "resolved" | "reviewed";
  incident: Incident;
}

type Listener<T> = (payload: T) => void;

interface LiveBus {
  connected: boolean;
  onEvents: (l: Listener<EventBatch>) => () => void;
  onIncident: (l: Listener<IncidentChange>) => () => void;
}

const LiveContext = createContext<LiveBus | null>(null);

/** One EventSource per tab, fanned out to subscribers. EventSource reconnects on its own. */
export function LiveProvider({ children, onSessionLost }: { children: ReactNode; onSessionLost: () => void }) {
  const eventListeners = useRef(new Set<Listener<EventBatch>>());
  const incidentListeners = useRef(new Set<Listener<IncidentChange>>());
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const es = new EventSource("/api/stream", { withCredentials: true });
    es.addEventListener("hello", () => setConnected(true));
    es.addEventListener("events", (m) => {
      const batch = JSON.parse((m as MessageEvent<string>).data) as EventBatch;
      eventListeners.current.forEach((l) => l(batch));
    });
    es.addEventListener("incident", (m) => {
      const change = JSON.parse((m as MessageEvent<string>).data) as IncidentChange;
      incidentListeners.current.forEach((l) => l(change));
    });
    es.onerror = () => {
      setConnected(false);
      if (es.readyState === EventSource.CLOSED) onSessionLost();
    };
    return () => es.close();
  }, [onSessionLost]);

  const bus: LiveBus = {
    connected,
    onEvents: (l) => {
      eventListeners.current.add(l);
      return () => eventListeners.current.delete(l);
    },
    onIncident: (l) => {
      incidentListeners.current.add(l);
      return () => incidentListeners.current.delete(l);
    },
  };
  return <LiveContext.Provider value={bus}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveBus {
  const bus = useContext(LiveContext);
  if (!bus) throw new Error("useLive outside LiveProvider");
  return bus;
}
