import { createContext, useContext } from "react";
import type { Me, Role } from "./types";

export const SessionContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(SessionContext);
  if (!me) throw new Error("useMe outside session");
  return me;
}

export function hasRole(me: Me, role: Role): boolean {
  return me.roles.includes(role);
}
