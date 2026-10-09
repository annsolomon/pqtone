/** Milestone C5: formatting for the review-metrics page (pure, unit-tested). */
import type { RuleReviewMetrics } from "./types";

export function percent(v: number | null): string {
  return v == null ? "–" : `${Math.round(v * 100)}%`;
}

/** "80% (49–94%)": the rate with its 95% interval, or a dash when nothing has been decided. */
export function rateWithInterval(r: Pick<RuleReviewMetrics, "confirmRate" | "confirmRateLow" | "confirmRateHigh">): string {
  if (r.confirmRate == null || r.confirmRateLow == null || r.confirmRateHigh == null) return "–";
  return `${percent(r.confirmRate)} (${Math.round(r.confirmRateLow * 100)}–${Math.round(r.confirmRateHigh * 100)}%)`;
}

export function seconds(v: number | null): string {
  if (v == null) return "–";
  const s = Math.round(v);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

const REASON_LABELS: Record<string, string> = {
  FALSE_POSITIVE: "False alarm",
  DUPLICATE: "Duplicate",
  EXPECTED_BEHAVIOUR: "Expected",
  TEST_EVENT: "Test or drill",
  OTHER: "Other",
  UNSPECIFIED: "No reason",
};

/** Top reasons first, "False alarm 3, Duplicate 1". */
export function reasons(r: Record<string, number>): string {
  const parts = Object.entries(r)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([k, n]) => `${REASON_LABELS[k] ?? k} ${n}`);
  return parts.length ? parts.join(", ") : "–";
}

/** Small samples are flagged rather than read as certainty (same spirit as the scorer's Q4 gate). */
export const MIN_DECIDED_FOR_ESTIMATE = 20;

export function sampleNote(r: Pick<RuleReviewMetrics, "decided">): string | null {
  if (r.decided === 0) return "No decisions yet.";
  if (r.decided < MIN_DECIDED_FOR_ESTIMATE) return `Only ${r.decided} decided; treat the rate as a rough guide.`;
  return null;
}
