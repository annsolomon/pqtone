import { describe, expect, it } from "vitest";
import { ordered, scoreCell, verdict } from "./scorecard";
import type { RuleScore } from "./types";

const base: RuleScore = {
  ruleId: "R-ABS-001", ruleVersion: "1.0.0", mode: "shadow", runs: 25, lastMeasuredAt: "2026-01-01T00:00:00Z", lastRunId: "run-a",
  lastScenario: "register_delay", tp: 25, fp: 0, fn: 0, precision: 1, recall: 1, precisionLow: 0.8668, recallLow: 0.8668,
  precisionMin: 0.9, recallMin: 0.95, minN: 20, minLowerBound: 0.8, ready: true, reasons: [],
};

describe("shadow scorecard (R6)", () => {
  it("shows the value, its lower bound and the bar", () => {
    expect(scoreCell(1, 0.8668, 0.9)).toBe("1.000 (≥ 0.867; needs 0.90)");
    expect(scoreCell(null, null, 0.9)).toBe("–");
  });

  it("says why a rule is not ready, and never offers to promote an enforce rule", () => {
    expect(verdict(base)).toEqual({ label: "Ready to propose for enforce", tone: "ready" });
    expect(verdict({ ...base, ready: false, reasons: ["only 5 samples; need 20"] }).label).toBe("Not yet: only 5 samples; need 20");
    expect(verdict({ ...base, mode: "enforce", ready: false, reasons: ["already enforce"] })).toEqual({ label: "Enforcing", tone: "info" });
  });

  it("lists shadow rules first", () => {
    const rules = [{ ...base, ruleId: "R-QUEUE-001", mode: "enforce" as const }, base, { ...base, ruleId: "R-AAA-001" }];
    expect(ordered(rules).map((r) => r.ruleId)).toEqual(["R-AAA-001", "R-ABS-001", "R-QUEUE-001"]);
  });
});
