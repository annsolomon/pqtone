import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AlertProvider } from "./alerts/AlertProvider";
import { ToastStack } from "./alerts/ToastStack";
import { ApiError, get, loginUrl } from "./api";
import { HealthBanner } from "./components/HealthBanner";
import { ShadowScorecard } from "./components/ShadowScorecard";
import { TopBar } from "./components/TopBar";
import { LiveProvider } from "./live";
import { AuditPage } from "./pages/AuditPage";
import { FloorPage } from "./pages/FloorPage";
import { IncidentDetailPage } from "./pages/IncidentDetailPage";
import { IncidentListPage } from "./pages/IncidentListPage";
import { ReviewMetricsPage } from "./pages/ReviewMetricsPage";
import { SessionContext, hasRole } from "./session";
import type { Me } from "./types";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Me>("/api/me").then(setMe).catch((e: unknown) => {
      if (!(e instanceof ApiError && e.status === 401)) setError("The console could not reach the server. Check that the stack is running.");
    });
  }, []);

  const onSessionLost = useCallback(() => {
    get<Me>("/api/me").catch(() => window.location.assign(loginUrl()));
  }, []);

  if (error) return <main className="page"><p className="error">{error}</p></main>;
  if (!me) return <main className="page"><p className="muted">Signing you in…</p></main>;
  if (me.roles.length === 0) {
    return <main className="page"><h1>No access yet</h1><p>Your account has no console role. Ask an administrator to grant viewer access.</p></main>;
  }
  const admin = hasRole(me, "admin");

  return (
    <SessionContext.Provider value={me}>
      <LiveProvider onSessionLost={onSessionLost}>
        <AlertProvider>
          <a className="skip-link" href="#content">Skip to main content</a>
          <TopBar />
          <HealthBanner />
          <div id="content" tabIndex={-1}>
            <Routes>
              <Route path="/" element={<FloorPage />} />
              <Route path="/review" element={
                <IncidentListPage title="Review queue" query="mode=enforce&status=OPEN,ACKNOWLEDGED,AUTO_RESOLVED&limit=200"
                  intro="Incidents waiting for a person to confirm or dismiss them, newest first."
                  empty="The queue is empty. New incidents appear here as soon as a rule fires." />} />
              <Route path="/incidents" element={
                <IncidentListPage title="All incidents" query="mode=enforce&limit=200"
                  intro="Every alerting incident, including reviewed and closed ones."
                  empty="No incidents have been raised yet." />} />
              <Route path="/incidents/:id" element={<IncidentDetailPage />} />
              {admin && <Route path="/shadow" element={
                <IncidentListPage title="Shadow rules" query="mode=shadow&limit=200"
                  intro="Shadow rules run silently so their accuracy can be measured before they alert anyone. Nothing here notifies staff."
                  empty="No shadow incidents yet."><ShadowScorecard /></IncidentListPage>} />}
              {admin && <Route path="/audit" element={<AuditPage />} />}
              {admin && <Route path="/metrics" element={<ReviewMetricsPage />} />}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
          <ToastStack />
        </AlertProvider>
      </LiveProvider>
    </SessionContext.Provider>
  );
}
