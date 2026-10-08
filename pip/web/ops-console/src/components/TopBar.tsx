import { NavLink } from "react-router-dom";
import { logout } from "../api";
import { useLive } from "../live";
import { hasRole, useMe } from "../session";

export function TopBar() {
  const me = useMe();
  const { connected } = useLive();
  const admin = hasRole(me, "admin");
  return (
    <header className="topbar">
      <div className="brand">
        <svg viewBox="0 0 32 32" aria-hidden="true" className="brand-mark">
          <rect x="3" y="3" width="26" height="26" rx="3" fill="none" stroke="currentColor" strokeWidth="2" />
          <rect x="7" y="7" width="8" height="8" fill="var(--live)" />
          <rect x="17" y="7" width="8" height="18" fill="none" stroke="currentColor" strokeWidth="2" />
          <circle cx="11" cy="21" r="2.5" fill="var(--open)" />
        </svg>
        <span>Store operations</span>
      </div>
      <nav aria-label="Main">
        <NavLink to="/" end>Floor</NavLink>
        <NavLink to="/review">Review queue</NavLink>
        <NavLink to="/incidents">All incidents</NavLink>
        {admin && <NavLink to="/shadow">Shadow rules</NavLink>}
        {admin && <NavLink to="/audit">Audit log</NavLink>}
      </nav>
      <div className="who">
        <span className={connected ? "pulse on" : "pulse"} title={connected ? "Receiving live data" : "Reconnecting"} />
        <span className="who-name">{me.name || me.username}</span>
        <span className="who-role">{me.roles.includes("admin") ? "admin" : me.roles.includes("reviewer") ? "reviewer" : me.roles.includes("operator") ? "operator" : "viewer"}</span>
        <button type="button" className="ghost" onClick={() => void logout()}>Sign out</button>
      </div>
    </header>
  );
}
