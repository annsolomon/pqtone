/** Milestone R6: formatting for the shadow-rule scorecard (pure, unit-tested). */
import type { RuleScore } from "./types";

export function scoreCell(value: number | null, low: number | null, min: number): string {
  if (value == null) return "–";
  const v = value.toFixed(3);
  return low == null ? `${v} (needs ${min.toFixed(2)})` : `${v} (≥ ${low.toFixed(3)}; needs ${min.toFixed(2)})`;
}

export function verdict(r: Pick<RuleScore, "ready" | "mode" | "reasons">): { label: string; tone: "ready" | "wait" | "info" } {
  if (r.mode !== "shadow") return { label: "Enforcing", tone: "info" };
  if (r.ready) return { label: "Ready to propose for enforce", tone: "ready" };
  return { label: `Not yet: ${r.reasons.join("; ")}`, tone: "wait" };
}

/** Shadow rules first, then by rule id. */
export function ordered(rules: RuleScore[]): RuleScore[] {
  return [...rules].sort((a, b) => (a.mode === b.mode ? a.ruleId.localeCompare(b.ruleId) : a.mode === "shadow" ? -1 : 1));
}
