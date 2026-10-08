import { useEffect, useState } from "react";
import { get } from "../api";
import { IncidentTable } from "../components/IncidentTable";
import { useLive } from "../live";
import type { Incident } from "../types";

interface Props {
  title: string;
  intro: string;
  query: string;
  empty: string;
}

export function IncidentListPage({ title, intro, query, empty }: Props) {
  const live = useLive();
  const [items, setItems] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => get<Incident[]>(`/api/incidents?${query}`).then((r) => alive && setItems(r)).catch((e: Error) => setError(e.message));
    void load();
    const off = live.onIncident(() => void load());
    return () => {
      alive = false;
      off();
    };
  }, [query, live]);

  return (
    <main className="page">
      <h1>{title}</h1>
      <p className="intro">{intro}</p>
      {error && <p className="error">{error}</p>}
      {items === null ? <p className="muted">Loading…</p> : <IncidentTable incidents={items} empty={empty} />}
    </main>
  );
}
